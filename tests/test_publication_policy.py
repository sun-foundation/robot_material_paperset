import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('sync_library',Path(__file__).parents[1]/'scripts'/'sync_library.py')
sync=importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class PublicationPolicyTests(unittest.TestCase):
    def decide(self,text='',licenses=None,note='题名吻合'):
        return sync.decide_license({'url':'https://doi.org/example'}, {'match_note':note},
             {'license':[{'URL':u} for u in licenses or []]},text)

    def test_school_subscription_is_not_redistribution(self):
        self.assertFalse(self.decide(licenses=['https://www.springer.com/tdm'])['allowed'])

    def test_version_specific_pdf_license_takes_priority(self):
        result=self.decide('This is an open access article under the CC BY-NC-ND license (http://creativecommons.org/licenses/by-nc-nd/3.0/).',
                           ['https://creativecommons.org/licenses/by-nc-nd/4.0/'])
        self.assertTrue(result['allowed'])
        self.assertEqual(result['license_url'],'https://creativecommons.org/licenses/by-nc-nd/3.0/')

    def test_preprint_does_not_inherit_journal_license(self):
        result=self.decide('arXiv:2304.06621 Preprint submitted to journal', ['https://creativecommons.org/licenses/by/4.0/'], '作者预印本')
        self.assertFalse(result['allowed'])

    def test_explicit_manuscript_license_is_usable(self):
        result=self.decide('This manuscript version is made available under the CC-BY-NC-ND 4.0 license http://creativecommons.org/licenses/by-nc-nd/4.0/',note='作者预印本')
        self.assertTrue(result['allowed'])

    def test_publisher_unedited_manuscript_with_explicit_license(self):
        result=self.decide('ORIGINAL UNEDITED MANUSCRIPT. This is an Open Access article distributed under the Creative Commons Attribution-NonCommercial License (https://creativecommons.org/licenses/by-nc/4.0/).',note='出版商未编辑原稿')
        self.assertTrue(result['allowed'])

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            sync.safe_relative('../../private.pdf')


if __name__=='__main__':
    unittest.main()
