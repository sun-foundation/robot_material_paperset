import hashlib
import json
from pathlib import Path
import re
import unittest
import zipfile

ROOT=Path(__file__).parents[1]


class ExportIntegrityTests(unittest.TestCase):
    def test_synced_files_preserve_real_license_status_and_matching_hash(self):
        rights=json.loads((ROOT/'licenses'/'publication_rights.json').read_text(encoding='utf-8'))
        records=json.loads((ROOT/'catalog'/'library.json').read_text(encoding='utf-8'))
        allowed={r['id']:r for r in rights if r['allowed']}
        by_id={r['id']:r for r in rights}
        receipt=json.loads((ROOT/'catalog'/'imports'/'2026-11-01_V4_all_pdfs.json').read_text(encoding='utf-8'))
        requested={r['id']:r for r in receipt['newly_synced']}
        linked={r['id']:r for r in records if r['public_pdf']}
        self.assertEqual(set(allowed)|set(requested),set(linked))
        actual=set()
        for i,r in linked.items():
            path=ROOT/r['public_pdf']
            self.assertTrue(path.is_file(),i)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),by_id[i]['sha256'],i)
            if i in allowed:
                self.assertTrue(r['license_url'].startswith('https://creativecommons.org/licenses/'),i)
            else:
                self.assertFalse(by_id[i]['allowed'],i)
                self.assertIsNone(r['license_url'],i)
                self.assertEqual(r['public_pdf'],requested[i]['file'],i)
                self.assertEqual(by_id[i]['sha256'],requested[i]['sha256'],i)
                self.assertFalse(requested[i]['open_license_verified'],i)
            self.assertTrue(r['authors'],i)
            actual.add(path.resolve())
        self.assertEqual(actual,{p.resolve() for p in (ROOT/'papers').rglob('*.pdf')})

    def test_public_catalog_does_not_leak_local_paths_or_session_tokens(self):
        pattern=re.compile(r'(?i)(?:[A-Z]:[\\/](?:Users|Codex)|X-Amz-Security-Token|authorization:\s*bearer|_sg%5B)')
        for folder in ['catalog','licenses']:
            for path in (ROOT/folder).rglob('*'):
                if not path.is_file():continue
                if path.suffix=='.xlsx':
                    with zipfile.ZipFile(path) as z:
                        text='\n'.join(z.read(n).decode('utf-8','replace') for n in z.namelist() if n.endswith('.xml'))
                elif path.suffix in ['.json','.csv','.md','.ris']:
                    text=path.read_text(encoding='utf-8-sig')
                else:continue
                self.assertIsNone(pattern.search(text),str(path))

    def test_summary_matches_catalog(self):
        s=json.loads((ROOT/'catalog'/'status.json').read_text(encoding='utf-8'))
        records=json.loads((ROOT/'catalog'/'library.json').read_text(encoding='utf-8'))
        self.assertEqual(s['records'],len(records))
        self.assertEqual(s['downloaded_locally'],sum(r['download_status']=='已下载' for r in records))
        self.assertEqual(s['public_pdfs'],sum(bool(r['public_pdf']) for r in records))


if __name__=='__main__':unittest.main()
