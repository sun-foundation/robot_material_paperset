import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]/'scripts'))
from import_fulltexts import check_license


class SavedVersionLicenseTests(unittest.TestCase):
    def test_reserved_copy_does_not_inherit_metadata_open_license(self):
        result = check_license({'url':'https://doi.org/example'},
            '2023 Elsevier Ltd. All rights reserved.',
            {'license':[{'URL':'https://creativecommons.org/licenses/by/4.0/'}]})
        self.assertFalse(result['allowed'])

    def test_article_explicit_license_without_hyperlink(self):
        result = check_license({'url':'https://doi.org/example'},
            'Open Access. This work is licensed under the Creative Commons Attribution 4.0 International License.', {})
        self.assertTrue(result['allowed'])
        self.assertEqual(result['license_url'], 'https://creativecommons.org/licenses/by/4.0/')

    def test_generic_wiley_oa_footer_is_not_an_article_license(self):
        result = check_license({'url':'https://doi.org/example'},
            '2025 Wiley-VCH GmbH. OA articles are governed by the applicable Creative Commons License', {})
        self.assertFalse(result['allowed'])


if __name__=='__main__':
    unittest.main()
