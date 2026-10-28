"""Publish a curated local literature library without exporting private state.

Only PDFs with evidence of a redistribution license are copied to the public
repository. The local originals, login sessions and download database stay local.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import quote, urlsplit

REPO = Path(__file__).resolve().parents[1]
EXPECTED_REMOTE = 'https://github.com/sun-foundation/robot_material_paperset.git'
CC = re.compile(r'https?://(?:www\.)?creativecommons\.org/licenses/(by(?:-nc)?(?:-nd|-sa)?)/(\d\.\d)(?:/|$)', re.I)
VERSION_MARKERS = ('arxiv:', 'preprint submitted', 'accepted manuscript', 'author manuscript', 'original unedited manuscript')
EXPORTS = ('近十年文献总表_镁铝钛与镁基复合材料.xlsx', '分类文献清单_2016-2026.md', '文献库_2016-2026.ris', '仓库同步说明.md')


def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')


def load_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_changed(path, data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes(data)
    temp.replace(path)
    return True


def dump_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def csv_bytes(headers, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def safe_relative(path):
    """Reject path traversal and anything outside this checkout."""
    p = (REPO / path).resolve()
    if not p.is_relative_to(REPO.resolve()):
        raise ValueError('Destination escapes repository')
    return p


def git(*args, check=True, timeout=180):
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    p = subprocess.run(['git', '-c', 'http.proxy=', '-c', 'https.proxy=', *args], cwd=REPO,
                       env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f'Git {args[0]} failed: {p.stderr.strip()[:1000]}')
    return p


def canonical_cc(url):
    match = CC.search(str(url))
    if not match:
        return None
    return f'https://creativecommons.org/licenses/{match[1].lower()}/{match[2]}/'


def decide_license(record, state, metadata, pdf_text):
    """Prefer the saved PDF's version-specific statement over Crossref."""
    text = pdf_text.lower()
    compact = re.sub(r'\s+', '', pdf_text)
    declaration = re.search(r'(?:this.{0,75}(?:open.access|creative commons)|manuscript version.{0,100}license)', text, re.S)
    pdf_licenses = list(dict.fromkeys(canonical_cc(m[0]) for m in CC.finditer(compact)))
    pdf_licenses = [u for u in pdf_licenses if u]
    if declaration and len(pdf_licenses) == 1:
        evidence = pdf_text[max(0, declaration.start()-20):declaration.end()+180]
        return {'allowed': True, 'license_url': pdf_licenses[0], 'evidence_source': 'saved_pdf',
                'evidence': re.sub(r'\s+', ' ', evidence)[:400]}
    different_version = any(v in text for v in VERSION_MARKERS) or any(v in (state.get('match_note') or '') for v in ('预印本', '未编辑原稿'))
    if different_version:
        return {'allowed': False, 'reason': '保存版本与出版版本不同，尚未核实该版本的再分发许可'}
    crossref = list(dict.fromkeys(u for entry in metadata.get('license', []) if (u := canonical_cc(entry.get('URL', '')))))
    if len(crossref) == 1:
        return {'allowed': True, 'license_url': crossref[0], 'evidence_source': record.get('source_url') or record['url'],
                'evidence': 'Crossref正式题录登记的Creative Commons许可；本地保存版本已核对题名/DOI。'}
    return {'allowed': False, 'reason': '尚未核实允许公开再分发的全文许可；仅公开题录与来源链接'}


def export_library(config):
    source = Path(config['source_dir']).resolve()
    work = Path(config['work_dir']).resolve()
    library = load_json(work / 'library.json')
    metadata = load_json(work / 'verified_metadata.json')
    cache_path = REPO / '.local' / 'license_cache.json'
    cache = load_json(cache_path, {})
    audit_seed = {x['id']: x for x in load_json(work / 'publication_license_audit.json', [])}
    with sqlite3.connect((work / 'downloads.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        states = {r['id']: dict(r) for r in conn.execute('SELECT * FROM downloads')}
    records = library['records']
    groups = library['groups']
    planned = set()
    public_records = []
    rights = []
    public_count = downloaded_count = 0
    public_bytes = 0
    for rec in records:
        r = dict(rec)
        state = states.get(r['id'], {})
        downloaded = state.get('status') == '已下载'
        r['download_status'] = state.get('status', '待下载')
        r['pages'] = state.get('pages') or None
        r['version_note'] = state.get('match_note') or ''
        r['public_pdf'] = None
        r['license_url'] = None
        if downloaded:
            downloaded_count += 1
            file = Path(state['file']).resolve()
            # A malicious or accidental database path cannot publish other files.
            if not file.is_relative_to(source / '论文全文') or file.suffix.lower() != '.pdf':
                raise ValueError(f"{r['id']}: source PDF is outside the literature directory")
            stat = file.stat()
            meta = metadata.get(r['doi'].lower(), {}).get('metadata', {})
            license_key = hashlib.sha256(dump_json(meta.get('license', [])).encode()).hexdigest()
            fingerprint = [stat.st_size, stat.st_mtime_ns, state['sha256'], license_key, state.get('match_note')]
            saved = cache.get(r['id'], {})
            if saved.get('fingerprint') == fingerprint:
                decision = saved['decision']
            else:
                digest = hashlib.sha256(file.read_bytes()).hexdigest()
                if digest != state['sha256']:
                    decision = {'allowed': False, 'reason': '本地PDF内容变化，需重新核对文件及许可'}
                else:
                    text = ''
                    try:
                        from pypdf import PdfReader
                        reader = PdfReader(file, strict=False)
                        text = '\n'.join(p.extract_text() or '' for p in reader.pages[:2])
                    except Exception:
                        text = '\n'.join(audit_seed.get(r['id'], {}).get('pdf_license_lines', []))
                    decision = decide_license(r, state, meta, text)
                cache[r['id']] = {'fingerprint': fingerprint, 'decision': decision}
            rights.append({'id': r['id'], 'title': r['title'], 'doi': r['doi'], 'authors': r['authors'],
                           'sha256': state['sha256'], 'version_note': r['version_note'], **decision})
            if decision['allowed']:
                rel = Path('papers') / f'{groups.index(r["group"])+1:02d}_{r["group"]}' / file.name
                dest = safe_relative(rel)
                if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != state['sha256']:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    temp = REPO / '.local' / 'copy.tmp'
                    temp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, temp)
                    if hashlib.sha256(temp.read_bytes()).hexdigest() != state['sha256']:
                        raise ValueError(f"{r['id']}: copy verification failed")
                    temp.replace(dest)
                r['public_pdf'] = rel.as_posix()
                r['license_url'] = decision['license_url']
                planned.add(rel.as_posix())
                public_count += 1
                public_bytes += stat.st_size
            else:
                r['publication_note'] = decision['reason']
        public_records.append(r)
    # Copy original editable bibliography exports, keeping the source untouched.
    for name in EXPORTS:
        path = source / name
        if path.exists():
            rel = 'catalog/' + name
            write_changed(safe_relative(rel), path.read_bytes())
            planned.add(rel)
    def output(rel, data):
        write_changed(safe_relative(rel), data)
        planned.add(rel)
    snapshot_updated = max((s.get('updated') or '' for s in states.values()), default='')
    summary = {'records': len(records), 'downloaded_locally': downloaded_count, 'public_pdfs': public_count,
               'local_only_pdfs': downloaded_count-public_count, 'pending_fulltext': len(records)-downloaded_count,
               'public_pdf_bytes': public_bytes, 'download_status_updated': snapshot_updated,
               'group_counts': [{'group': g, 'records': sum(r['group']==g for r in public_records),
                                 'downloaded': sum(r['group']==g and r['download_status']=='已下载' for r in public_records),
                                 'public_pdfs': sum(r['group']==g and bool(r['public_pdf']) for r in public_records)} for g in groups]}
    output('catalog/library.json', dump_json(public_records))
    output('catalog/status.json', dump_json(summary))
    output('licenses/publication_rights.json', dump_json(rights))
    headers = ['编号','年份','论文题名','作者','期刊','DOI','分类','本地下载状态','页数','GitHub全文','全文许可','版本与核对说明','官方入口']
    output('catalog/全文下载进度.csv', csv_bytes(headers, [[r['id'],r['year'],r['title'],r['authors_short'],r['journal'],r['doi'],r['group'],r['download_status'],r['pages'] or '',r['public_pdf'] or '',r['license_url'] or '',r['version_note'],r['url']] for r in public_records]))
    output('licenses/全文公开许可清单.csv', csv_bytes(['编号','题名','DOI','可公开PDF','许可','证据来源','说明','SHA256'],
        [[r['id'],r['title'],r['doi'],'是' if r['allowed'] else '否',r.get('license_url',''),r.get('evidence_source',''),r.get('evidence') or r.get('reason',''),r['sha256']] for r in rights]))
    index = ['# 已取得全文索引', '', f'本地已取得 {downloaded_count} 篇，其中 {public_count} 篇已核实再分发许可并提供原样PDF。',
             '其余已下载论文保留题录、版本说明和官方入口；本地原始文件保持不变。', '']
    attributions = ['# PDF 署名与许可', '', '每份PDF保持原始文件内容及署名，文件名仅用于分类。以下许可分别适用于对应论文。',
                    '本仓库用于非商业学术文献整理；含NC许可的文件不得据此推定可用于商业用途，含ND许可的文件按原样提供。', '']
    for group in groups:
        index += [f'## {group}', '']
        for r in public_records:
            if r['group'] != group or r['download_status'] != '已下载':
                continue
            target = '../' + quote(r['public_pdf'], safe='/') if r['public_pdf'] else r['url']
            label = '仓库PDF' if r['public_pdf'] else '官方入口；本地有全文，仓库不分发PDF'
            index += [f"- **{r['id']}** {r['title']} ({r['year']}) — [{label}]({target})；{r['pages']}页。{r['version_note']}"]
            if r['public_pdf']:
                attributions += [f"## {r['id']} — {r['title']}", '', f"作者：{'; '.join(r['authors'])}", '',
                    f"出处：{r['journal']}，{r['year']}。DOI：[原文]({r['url']})。", '',
                    f"许可：[{r['license_url']}]({r['license_url']})。", '',
                    f"文件：[原样PDF](../{quote(r['public_pdf'],safe='/')})。", '']
        index += ['']
    output('catalog/已下载全文索引.md', '\n'.join(index))
    output('licenses/ATTRIBUTIONS.md', '\n'.join(attributions))
    # Preserve local Markdown notes while converting file links to public-safe URLs.
    original = source / '全文下载说明.md'
    if original.exists():
        text = original.read_text(encoding='utf-8')
        for r in public_records:
            st = states.get(r['id'], {})
            if st.get('file'):
                rel = Path(st['file']).resolve().relative_to(source).as_posix()
                target = '../' + quote(r['public_pdf'],safe='/') if r['public_pdf'] else r['url']
                text = text.replace('](' + rel + ')', '](' + target + ')')
        output('catalog/本地下载说明.md', text)
    readme = f'''# 人形机器人轻质材料文献库

围绕人形/双足机器人结构轻量化，以及镁合金、铝合金、钛合金、镁基复合材料的性能与制造整理文献。范围为2016—2026年。

## 当前内容

- 正式题录：**{len(records)}篇**。
- 本地已取得全文：**{downloaded_count}篇**。
- 本仓库提供已核实开放再分发许可的原样PDF：**{public_count}篇**。
- 本地已取得、但本仓库仅提供题录和入口：**{downloaded_count-public_count}篇**。
- 尚未取得全文：**{len(records)-downloaded_count}篇**。

下载状态最后更新：{snapshot_updated}。本地取得全文不等于允许公开再分发，二者在目录中分别列出。

## 使用入口

- [Excel文献总表](catalog/近十年文献总表_镁铝钛与镁基复合材料.xlsx)
- [分类文献清单](catalog/分类文献清单_2016-2026.md)
- [RIS文献库](catalog/文献库_2016-2026.ris)
- [全部题录JSON](catalog/library.json)
- [全文下载与公开状态CSV](catalog/全文下载进度.csv)
- [已取得全文索引](catalog/已下载全文索引.md)
- [论文全文文件夹](papers/)
- [逐篇许可及署名](licenses/ATTRIBUTIONS.md)
- [完整许可核验清单](licenses/全文公开许可清单.csv)
- [本地自动同步说明](docs/AUTO_SYNC.md)

## 分类

| 分类 | 题录 | 本地全文 | 仓库PDF |
|---|---:|---:|---:|
'''
    for g in summary['group_counts']:
        readme += f"| {g['group']} | {g['records']} | {g['downloaded']} | {g['public_pdfs']} |\n"
    readme += '''
## 证据边界与文件版本

机器人结构论文与材料/工艺支撑论文分别分类。材料性能研究不代表已经在商业人形机器人中实际采用。
题录以Crossref及期刊页面核验；“已下载”表示已读取并核对题名或DOI，不表示已对全文进行系统评审。
预印本、出版商未编辑原稿等版本在下载目录中注明。

## PDF 与许可

PDF通过Git LFS存储。使用Git取得全部仓库PDF：

```sh
git lfs install
git clone https://github.com/sun-foundation/robot_material_paperset.git
cd robot_material_paperset
git lfs pull
```

PDF逐篇沿用原作者/出版商的许可，本仓库没有对全部论文授予统一的新许可。
未核实再分发许可、仅有学校订阅访问权或保存版本许可不明的PDF不会自动公开。
论文原始PDF保持字节不变，署名、来源、许可和SHA256见`licenses/`。
引用请使用原始论文的DOI和正式题录。

## 自动更新

按本地文献库变更同步，不自动检索新论文。每5秒检测一次，变更稳定10秒后更新目录并提交上传。
本机需要开机、用户登录并联网；断网或认证失效时保留本地文件并重试，详细状态保留在本机`.local/`。
自动同步只管理本仓库的文献导出文件，不上传学校账号、Cookie、验证码、浏览器配置、临时日志或本地绝对路径。
'''
    output('README.md', readme)
    previous = load_json(REPO / '.local' / 'managed_files.json', [])
    for rel in set(previous) - planned:
        path = safe_relative(rel)
        if path.is_file() and (rel.startswith('papers/') or rel.startswith('catalog/') or rel.startswith('licenses/')):
            path.unlink()  # A managed file only; its history remains recoverable in Git.
    write_changed(cache_path, dump_json(cache))
    write_changed(REPO / '.local' / 'managed_files.json', dump_json(sorted(planned)))
    return summary


def publish(config, build_only=False, allow_dirty=False):
    if not build_only:
        remote = git('remote', 'get-url', 'origin').stdout.strip()
        if remote.removesuffix('.git') != EXPECTED_REMOTE.removesuffix('.git'):
            raise RuntimeError('origin differs from the authorized repository')
        if not allow_dirty and git('status', '--porcelain').stdout.strip():
            raise RuntimeError('工作区存在未提交修改，自动同步暂停以保护人工编辑')
        git('pull', '--ff-only', 'origin', 'main')
    summary = export_library(config)
    if not build_only:
        managed = load_json(REPO / '.local' / 'managed_files.json', [])
        # Only the declared generated paths can be staged by the background task.
        pathspec = sorted(set(managed + ['catalog', 'licenses', 'papers', 'README.md']))
        git('add', '-A', '--', *pathspec)
        if git('diff', '--cached', '--quiet', check=False).returncode == 1:
            git('commit', '-m', f"同步文献库：{summary['records']}篇题录，{summary['public_pdfs']}篇可公开全文")
        git('push', 'origin', 'main', timeout=1800)
        local = git('rev-parse', 'HEAD').stdout.strip()
        remote = git('ls-remote', 'origin', 'refs/heads/main').stdout.split()[0]
        if local != remote:
            raise RuntimeError('Remote main does not match the local commit after push')
        summary['verified_commit'] = local
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=REPO / '.local' / 'config.json')
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--allow-dirty', action='store_true')
    args = parser.parse_args()
    config = load_json(args.config)
    if not config:
        raise SystemExit('Missing local configuration; see docs/AUTO_SYNC.md')
    print(dump_json(publish(config, args.build_only, args.allow_dirty)))


if __name__ == '__main__':
    main()
