"""Sync this batch's archived PDFs on the user's explicit instruction.

File synchronization and open-license verification are separate facts. This
command retains negative/unknown license decisions and does not grant a license.
"""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote

import sync_library as sync

REPO = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(archive):
    archive = archive.resolve()
    batch = sync.load_json(REPO/'catalog/imports/2026-11-01_V3.json')
    records = sync.load_json(REPO/'catalog/library.json')
    by_id = {r['id']:r for r in records}
    rights = sync.load_json(REPO/'licenses/publication_rights.json')
    right_by_id = {r['id']:r for r in rights}
    summary = sync.load_json(REPO/'catalog/status.json')
    before = dict(summary)
    pdfs = list((archive/'论文全文').rglob('*.pdf'))
    checked = []
    # Validate the whole batch before copying or updating the catalog.
    for entry in batch['items']:
        files = [p for p in pdfs if p.name.startswith('['+entry['id']+']')]
        if len(files)!=1 or not files[0].resolve().is_relative_to(archive):
            raise ValueError('Ambiguous or out-of-scope archived PDF: '+entry['id'])
        source = files[0]
        if digest(source)!=entry['sha256']:
            raise ValueError('Archive SHA256 mismatch: '+entry['id'])
        record = by_id[entry['id']]
        rel = Path('papers')/source.relative_to(archive/'论文全文')
        dest = sync.safe_relative(rel)
        if dest.exists() and digest(dest)!=entry['sha256']:
            raise ValueError('Different destination bytes: '+entry['id'])
        checked.append((entry,record,source,rel,dest))

    previous_receipt=sync.load_json(REPO/'catalog/imports/2026-11-01_V4_all_pdfs.json',{})
    recorded_additions={r['id']:r for r in previous_receipt.get('newly_synced',[])}
    additional = []
    for entry,record,source,rel,dest in checked:
        was_synced = bool(record['public_pdf'])
        dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():
            shutil.copyfile(source,dest)
        if digest(dest)!=entry['sha256']:
            raise ValueError('Copied SHA256 mismatch: '+entry['id'])
        record['public_pdf'] = rel.as_posix()
        record['publication_note'] = ('已核验本PDF版本的开放许可；原始字节同步' if entry['allowed']
            else '按用户明确要求同步原始PDF；未核实开放再分发许可，原版权声明保留')
        if not entry['allowed']:
            record['license_url'] = None
            right = right_by_id[entry['id']]
            right['reason'] = re.sub(r'；仅(?:同步题录和下载状态|公开题录与来源链接)$','',right['reason'])
            right['synced_pdf'] = rel.as_posix()
            right['sync_basis'] = '用户明确要求同步本次归档的全部PDF；不改变原许可核验结论'
        if not was_synced:
            additional.append({'id':entry['id'],'sha256':entry['sha256'],'file':rel.as_posix(),
                               'open_license_verified':entry['allowed']})
    recorded_additions.update({r['id']:r for r in additional})

    summary.update(public_pdfs=sum(bool(r['public_pdf']) for r in records),
                   local_only_pdfs=sum(r['download_status']=='已下载' and not r['public_pdf'] for r in records),
                   public_pdf_bytes=sum((REPO/r['public_pdf']).stat().st_size for r in records if r['public_pdf']),
                   open_license_verified_pdfs=sum(bool(r['public_pdf']) and right_by_id[r['id']]['allowed'] for r in records),
                   synced_without_verified_open_license=sum(bool(r['public_pdf']) and not right_by_id[r['id']]['allowed'] for r in records))
    for group in summary['group_counts']:
        group['public_pdfs'] = sum(r['group']==group['group'] and bool(r['public_pdf']) for r in records)
    def output(relative,data):
        sync.write_changed(sync.safe_relative(relative),data)
    output('catalog/library.json',sync.dump_json(records))
    output('catalog/status.json',sync.dump_json(summary))
    output('licenses/publication_rights.json',sync.dump_json(rights))
    headers=['编号','年份','论文题名','作者','期刊','DOI','分类','本地下载状态','页数','GitHub全文','全文许可','版本与核对说明','官方入口']
    output('catalog/全文下载进度.csv',sync.csv_bytes(headers,[[r['id'],r['year'],r['title'],r['authors_short'],r['journal'],r['doi'],r['group'],r['download_status'],r['pages'] or '',r['public_pdf'] or '',r['license_url'] or '',r['version_note'],r['url']] for r in records]))
    output('licenses/全文公开许可清单.csv',sync.csv_bytes(['编号','题名','DOI','开放许可已核验','已同步PDF','许可','证据来源','说明','SHA256'],
        [[r['id'],r['title'],r['doi'],'是' if r['allowed'] else '否','是' if by_id[r['id']]['public_pdf'] else '否',r.get('license_url',''),r.get('evidence_source',''),r.get('evidence') or r.get('reason',''),r['sha256']] for r in rights]))

    index=['# 已取得全文索引','',f"已取得全文{summary['downloaded_locally']}篇，仓库已同步{summary['public_pdfs']}份PDF。",'',
           f"其中{summary['open_license_verified_pdfs']}份已核验开放许可，{summary['synced_without_verified_open_license']}份按用户明确要求同步并保留未核实许可标记。",'']
    for group in summary['group_counts']:
        index+=['## '+group['group'],'']
        for r in records:
            if r['group']!=group['group'] or r['download_status']!='已下载':continue
            url='../'+quote(r['public_pdf'],safe='/') if r['public_pdf'] else r['url']
            label='仓库PDF' if r['public_pdf'] else '官方入口；原文献库已登记下载'
            if r['public_pdf'] and not right_by_id[r['id']]['allowed']:label+='（开放许可未核实）'
            index.append(f"- **{r['id']}** {r['title']}（{r['year']}）— [{label}]({url})；{r['pages']}页。")
        index.append('')
    output('catalog/已下载全文索引.md','\n'.join(index).rstrip()+'\n')
    attributions=(REPO/'licenses/ATTRIBUTIONS.md').read_text('utf8').split('\n# 按用户要求同步、开放许可未核实的原始PDF')[0].rstrip()
    attributions+='\n\n# 按用户要求同步、开放许可未核实的原始PDF\n\n以下文件保留原版权声明，未被标注为CC授权。\n'
    for entry,record,source,rel,dest in checked:
        if entry['allowed']:continue
        attributions+=f"\n## {record['id']} — {record['title']}\n\n作者：{'; '.join(record['authors'])}。\n\n出处：{record['journal']}，{record['year']}。[DOI官方入口]({record['url']})。\n\n开放许可：未核实。原始版权声明保持不变。\n\n[原样PDF](../{quote(rel.as_posix(),safe='/')})。SHA256：`{entry['sha256']}`。\n"
    output('licenses/ATTRIBUTIONS.md',attributions.rstrip()+'\n')
    readme=(REPO/'README.md').read_text('utf8')
    readme=readme.replace(f"本仓库提供已核实开放再分发许可的原样PDF：**{before['public_pdfs']}篇**",f"本仓库已同步原样PDF：**{summary['public_pdfs']}篇**（开放许可已核验{summary['open_license_verified_pdfs']}篇，未核实{summary['synced_without_verified_open_license']}篇）")
    readme=readme.replace(f"仅提供题录和入口：**{before['local_only_pdfs']}篇**",f"仅提供题录和入口：**{summary['local_only_pdfs']}篇**")
    readme=readme.replace('PDF逐篇沿用原作者/出版商的许可，本仓库没有对全部论文授予统一的新许可。\n未核实再分发许可、仅有学校订阅访问权或保存版本许可不明的PDF不会自动公开。',
        'PDF逐篇保留原作者/出版商的版权及许可声明，本仓库没有对全部论文授予统一的新许可。\n2026年11月1日按用户明确要求同步本次归档的全部22份PDF，其中14份的开放再分发许可尚未核实。开放许可核验与文件同步状态分别记录。')
    for g in summary['group_counts']:
        readme=re.sub(r'\| '+re.escape(g['group'])+r' \| \d+ \| \d+ \| \d+ \|',f"| {g['group']} | {g['records']} | {g['downloaded']} | {g['public_pdfs']} |",readme)
    if '全部PDF同步记录 V4' not in readme:
        readme=readme.replace('## 使用入口\n\n','## 使用入口\n\n- [全部PDF同步记录 V4](catalog/全部PDF同步记录_V4_2026-11-01.md)\n')
    readme=readme.replace('[本次归档与同步记录 V3]','[归档与同步记录 V3（历史）]')
    output('README.md',readme)
    output('catalog/imports/2026-11-01_V4_all_pdfs.json',sync.dump_json({'updated_at':sync.now(),
        'instruction':'用户明确要求：同步所有的pdf','batch_ids':[x[0]['id'] for x in checked],
        'newly_synced':[recorded_additions[k] for k in sorted(recorded_additions)],'license_verification_unchanged':True}))
    report=['# 全部PDF同步记录 V4','',f"本次归档22份PDF已全部同步。仓库现有{summary['public_pdfs']}份PDF。",'',
            '原文件内容、版权声明与许可核验结论保持不变。','',
            '| 编号 | 论文题名与PDF | 开放许可 |','|---|---|---|']
    for entry,record,source,rel,dest in sorted(checked,key=lambda x:x[0]['id']):
        license_label=record['license_url'] if entry['allowed'] else '未核实；保留原版权声明'
        report.append(f"| {record['id']} | [{record['title'].replace('|',' ')}](../{quote(rel.as_posix(),safe='/')}) | {license_label} |")
    report+=['', '另有45篇为原文献库的历史下载记录，其原始文件不在本次归档或当前克隆中，仍仅提供题录入口。', '']
    output('catalog/全部PDF同步记录_V4_2026-11-01.md','\n'.join(report))
    print(sync.dump_json({'batch_pdfs':len(checked),'newly_synced':len(additional),'repository_pdfs':summary['public_pdfs'],
                         'verified_open_licenses':summary['open_license_verified_pdfs']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path,required=True)
    args=parser.parse_args()
    main(args.archive)
