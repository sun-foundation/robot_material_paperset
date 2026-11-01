"""Merge verified local PDFs into an existing catalog without losing prior records.

The input manifest and local archive stay outside the public catalog. Only
version-specific licensed PDFs and path-free bibliographic state are published.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote

from pypdf import PdfReader
import sync_library as sync

REPO = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_license(record, text, metadata):
    compact = re.sub(r'\s+', '', text)
    explicit = re.search(r'ThisworkislicensedundertheCreativeCommonsAttribution4\.0InternationalLicense', compact, re.I)
    if explicit:
        return {'allowed': True, 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
                'evidence_source': 'saved_pdf',
                'evidence': 'PDF首页明确声明：This work is licensed under the Creative Commons Attribution 4.0 International License.'}
    decision = sync.decide_license(record, {'match_note': '正式论文PDF；题名已核对'}, metadata, text)
    # A generic publisher metadata license must not overrule a saved copy's
    # explicit reserved-rights statement. A PDF-specific CC statement can.
    reserved = re.search(r'all\s+rights(?:\s+are)?\s+reserved|all\s+rights,\s+including', text, re.I)
    if reserved and decision.get('evidence_source') != 'saved_pdf':
        return {'allowed': False, 'reason': '所保存PDF未声明开放再分发许可，并标注保留版权；仅同步题录和下载状态'}
    return decision


def validate(manifest, metadata_dir):
    records = sync.load_json(REPO/'catalog/library.json')
    by_id = {r['id']: r for r in records}
    prepared = []
    for item in manifest:
        record = by_id[item['id']]
        source = Path(item['file']).resolve()
        if digest(source) != item['sha256']:
            raise ValueError(f"{item['id']}: source SHA256 differs")
        reader = PdfReader(source, strict=False)
        text = '\n'.join(p.extract_text() or '' for p in reader.pages[:2])
        normalize = lambda value: re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', value.lower())
        if normalize(record['title']) not in normalize(text) and normalize(record['doi']) not in normalize(text):
            raise ValueError(f"{item['id']}: title and DOI not confirmed")
        metadata = sync.load_json(metadata_dir/(item['id']+'.json'), {}).get('crossref', {})
        decision = check_license(record, text, metadata)
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', record['title']).strip(' .')[:105]
        name = f"[{record['id']}] {record['year']} - {title}.pdf"
        prepared.append({'record': record, 'source': source, 'pages': len(reader.pages),
                         'sha256': item['sha256'], 'filename': name, 'decision': decision})
    if len({x['record']['id'] for x in prepared}) != len(prepared):
        raise ValueError('Duplicate record IDs')
    return records, prepared


def import_files(manifest, metadata_dir, archive_dir, report_file, apply=False):
    records, prepared = validate(manifest, metadata_dir)
    old_summary = sync.load_json(REPO/'catalog/status.json')
    groups = [g['group'] for g in old_summary['group_counts']]
    public_receipt = [{'id': p['record']['id'], 'title': p['record']['title'], 'doi': p['record']['doi'],
                       'pages': p['pages'], 'sha256': p['sha256'], **p['decision']} for p in prepared]
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(sync.dump_json(public_receipt), encoding='utf8')
    if not apply:
        return {'files': len(prepared), 'public': sum(p['decision']['allowed'] for p in prepared),
                'local_only': sum(not p['decision']['allowed'] for p in prepared)}

    timestamp = sync.now()
    rights = {r['id']: r for r in sync.load_json(REPO/'licenses/publication_rights.json')}
    for p in prepared:
        record = p['record']
        folder = f"{groups.index(record['group'])+1:02d}_{record['group']}"
        local = archive_dir/'论文全文'/folder/p['filename']
        local.parent.mkdir(parents=True, exist_ok=True)
        if local.exists() and digest(local) != p['sha256']:
            raise ValueError(f'{local.name}: refusing to overwrite different bytes')
        if not local.exists():
            shutil.copyfile(p['source'], local)
        if digest(local) != p['sha256']:
            raise ValueError(f"{record['id']}: archive hash mismatch")
        record.update(download_status='已下载', pages=p['pages'],
                      version_note='原始PDF可读取；题名或DOI核对吻合；2026-11-01归档')
        decision = p['decision']
        if decision['allowed']:
            relative = Path('papers')/folder/p['filename']
            public = sync.safe_relative(relative)
            public.parent.mkdir(parents=True, exist_ok=True)
            if public.exists() and digest(public) != p['sha256']:
                raise ValueError(f'{public.name}: refusing to replace a different public PDF')
            if not public.exists():
                shutil.copyfile(p['source'], public)
            assert digest(public) == p['sha256']
            record.update(public_pdf=relative.as_posix(), license_url=decision['license_url'],
                          publication_note='已核验本PDF版本的开放许可；原始字节公开')
        else:
            if record.get('public_pdf'):
                raise ValueError(f"{record['id']}: existing public PDF needs separate review")
            record.update(public_pdf=None, license_url=None, publication_note=decision['reason'])
        rights[record['id']] = {'id':record['id'], 'title':record['title'], 'doi':record['doi'],
                                'authors':record['authors'], 'sha256':p['sha256'],
                                'version_note':record['version_note'], **decision}
    downloaded = sum(r['download_status']=='已下载' for r in records)
    public_count = sum(bool(r['public_pdf']) for r in records)
    summary = {**old_summary, 'records':len(records), 'downloaded_locally':downloaded,
               'public_pdfs':public_count, 'local_only_pdfs':downloaded-public_count,
               'pending_fulltext':len(records)-downloaded,
               'public_pdf_bytes':sum((REPO/r['public_pdf']).stat().st_size for r in records if r['public_pdf']),
               'download_status_updated':timestamp,
               'group_counts':[{'group':g, 'records':sum(r['group']==g for r in records),
                                'downloaded':sum(r['group']==g and r['download_status']=='已下载' for r in records),
                                'public_pdfs':sum(r['group']==g and bool(r['public_pdf']) for r in records)} for g in groups]}
    def output(relative, data):
        sync.write_changed(sync.safe_relative(relative), data)
    output('catalog/library.json', sync.dump_json(records))
    output('catalog/status.json', sync.dump_json(summary))
    output('licenses/publication_rights.json', sync.dump_json([rights[k] for k in sorted(rights)]))
    headers = ['编号','年份','论文题名','作者','期刊','DOI','分类','本地下载状态','页数','GitHub全文','全文许可','版本与核对说明','官方入口']
    output('catalog/全文下载进度.csv', sync.csv_bytes(headers, [[r['id'],r['year'],r['title'],r['authors_short'],r['journal'],r['doi'],r['group'],r['download_status'],r['pages'] or '',r['public_pdf'] or '',r['license_url'] or '',r['version_note'],r['url']] for r in records]))
    output('licenses/全文公开许可清单.csv',sync.csv_bytes(['编号','题名','DOI','可公开PDF','许可','证据来源','说明','SHA256'],
        [[r['id'],r['title'],r['doi'],'是' if r['allowed'] else '否',r.get('license_url',''),r.get('evidence_source',''),r.get('evidence') or r.get('reason',''),r['sha256']] for r in sorted(rights.values(),key=lambda r:r['id'])]))
    index = ['# 已取得全文索引', '', f'本地已取得{downloaded}篇，其中{public_count}篇提供已核验开放许可的原样PDF。', '',
             '其余已取得全文保留题录、页数、核对状态及官方入口。', '']
    attributions = ['# PDF 署名与许可', '', '每份PDF保持原始字节及署名，文件名仅用于分类。许可分别适用于对应论文。', '',
                    '含NC许可的文件用于非商业学术整理；含ND许可的文件按原样提供。第三方材料的另行许可声明仍然适用。', '']
    for group in groups:
        index += ['## '+group,'']
        for r in records:
            if r['group']!=group or r['download_status']!='已下载':
                continue
            target = '../'+quote(r['public_pdf'],safe='/') if r['public_pdf'] else r['url']
            label = '仓库PDF' if r['public_pdf'] else '官方入口；本地已取得全文'
            index += [f"- **{r['id']}** {r['title']}（{r['year']}）— [{label}]({target})；{r['pages']}页。{r['version_note']}"]
            if r['public_pdf']:
                attributions += [f"## {r['id']} — {r['title']}",'',f"作者：{'; '.join(r['authors'])}",'',
                                 f"出处：{r['journal']}，{r['year']}。DOI：[原文]({r['url']})。",'',
                                 f"许可：[{r['license_url']}]({r['license_url']})。",'',
                                 f"文件：[原样PDF](../{quote(r['public_pdf'],safe='/')})。",'']
        index += ['']
    output('catalog/已下载全文索引.md','\n'.join(index).rstrip()+'\n')
    output('licenses/ATTRIBUTIONS.md','\n'.join(attributions).rstrip()+'\n')
    statuses = Counter(r['download_status'] for r in records)
    local_notes = ['# 论文全文下载进度','',f'更新时间：{timestamp}','',f'文献总数：{len(records)}篇。','',
                   '| 状态 | 篇数 |','|---|---:|']
    local_notes += [f'| {status} | {count} |' for status,count in statuses.items()]
    local_notes += ['', '已取得全文的记录及入口见[已下载全文索引](已下载全文索引.md)。', '',
                    f'本次合并22篇，其中用户提供17篇、此前核验下载5篇。另有45篇为此前文献库登记的本地全文，其原始文件未包含在当前克隆中。', '']
    output('catalog/本地下载说明.md','\n'.join(local_notes))
    pending = ['# 未下载文献链接 V3','',f'更新：{timestamp}。尚未取得全文{len(records)-downloaded}篇。','']
    for group in groups:
        items = [r for r in records if r['group']==group and r['download_status']!='已下载']
        pending += [f'## {group}（{len(items)}篇）','','| 编号 | 年份 | 题名与官方入口 |','|---|---:|---|']
        pending += [f"| {r['id']} | {r['year']} | [{r['title'].replace('|',' ')}]({r['url']}) |" for r in items]
        pending += ['']
    output('catalog/未下载文献链接_V3_2026-11-01.md','\n'.join(pending))
    (archive_dir/'未下载文献链接_V3.md').write_text('\n'.join(pending),encoding='utf8')
    output('catalog/imports/2026-11-01_V3.json',sync.dump_json({'updated_at':timestamp,'user_files':17,'previous_downloads':5,'items':public_receipt}))
    receipt = ['# 文献归档与同步记录 V3','',f'更新：{timestamp}。用户提供17篇，合并此前已核验5篇，共新增22篇。','',
               f"新增公开PDF：{sum(p['decision']['allowed'] for p in prepared)}篇；其余新增全文已归档到本地。",'',
               f'累计已取得全文{downloaded}篇，可公开PDF {public_count}篇，尚缺{len(records)-downloaded}篇。','',
               '| 编号 | 页数 | 题名 | GitHub同步内容 |','|---|---:|---|---|']
    for p in sorted(prepared,key=lambda p:p['record']['id']):
        r = p['record']; target = '../'+quote(r['public_pdf'],safe='/') if r['public_pdf'] else r['url']
        label = '题录、下载状态和原样PDF' if p['decision']['allowed'] else '题录与下载状态；全文仅在本地'
        receipt += [f"| {r['id']} | {p['pages']} | [{r['title'].replace('|',' ')}]({target}) | {label} |"]
    receipt += ['', '论文原始文件不作内容修改，逐篇SHA256及许可证据见同期JSON记录。', '']
    output('catalog/归档同步记录_V3_2026-11-01.md','\n'.join(receipt))
    # The local receipt links to local originals, not to a nonexistent repository path.
    local_receipt = ['# 本地文献归档 V3','',f'共归档{len(prepared)}篇原始PDF，分类及编号对应文献库。','']
    for p in sorted(prepared,key=lambda p:p['record']['id']):
        r=p['record'];folder=f"{groups.index(r['group'])+1:02d}_{r['group']}"
        target=Path('论文全文')/folder/p['filename']
        local_receipt.append(f"- **{r['id']}** [{r['title']}]({quote(target.as_posix(),safe='/')})（{p['pages']}页）")
    (archive_dir/'归档清单_V3.md').write_text('\n'.join(local_receipt)+'\n',encoding='utf8')
    readme = (REPO/'README.md').read_text('utf8')
    for key,old,new in [('downloaded_locally',old_summary['downloaded_locally'],downloaded),('public_pdfs',old_summary['public_pdfs'],public_count),
                        ('local_only_pdfs',old_summary['local_only_pdfs'],downloaded-public_count),('pending_fulltext',old_summary['pending_fulltext'],len(records)-downloaded)]:
        labels={'downloaded_locally':'本地已取得全文','public_pdfs':'本仓库提供已核实开放再分发许可的原样PDF','local_only_pdfs':'本地已取得、但本仓库仅提供题录和入口','pending_fulltext':'尚未取得全文'}
        readme=readme.replace(f"- {labels[key]}：**{old}篇**。",f"- {labels[key]}：**{new}篇**。")
    readme=re.sub(r'下载状态最后更新：[^。]+。',f'下载状态最后更新：{timestamp}。',readme)
    for g in summary['group_counts']:
        readme=re.sub(r'\| '+re.escape(g['group'])+r' \| \d+ \| \d+ \| \d+ \|',f"| {g['group']} | {g['records']} | {g['downloaded']} | {g['public_pdfs']} |",readme)
    entry='- [本次归档与同步记录 V3](catalog/归档同步记录_V3_2026-11-01.md)\n- [未下载文献链接 V3](catalog/未下载文献链接_V3_2026-11-01.md)\n'
    if '未下载文献链接 V3' not in readme:
        readme=readme.replace('## 使用入口\n\n','## 使用入口\n\n'+entry)
    output('README.md',readme)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--metadata-dir',type=Path,required=True)
    parser.add_argument('--archive-dir',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text('utf8'))
    result=import_files(manifest,args.metadata_dir,args.archive_dir,args.report,args.apply)
    print(sync.dump_json(result))
