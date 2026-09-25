import tempfile
import unittest
from pathlib import Path

from scimirror.semantic_fingerprint import (json_fingerprint, records_fingerprint,
                                             source_fingerprint, strict_json_text,
                                             combine_fingerprint)


class SemanticFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def test_json_layout_and_line_endings_are_semantically_equal(self):
        a=self.root/'a.json'; b=self.root/'b.json'
        a.write_bytes(b'{"b":2,"a":"x\\ny"}\r\n'); b.write_bytes(b'{\n  "a": "x\\ny",\n  "b": 2\n}\n')
        self.assertNotEqual(json_fingerprint(a)['raw_sha256'],json_fingerprint(b)['raw_sha256'])
        self.assertEqual(json_fingerprint(a)['semantic_sha256'],json_fingerprint(b)['semantic_sha256'])

    def test_csv_column_and_row_order_are_semantically_equal_for_queries(self):
        a=self.root/'a.csv'; b=self.root/'b.csv'
        a.write_text('query_id,query_text,intent\nq2,B,j\nq1,A,i\n',encoding='utf-8')
        b.write_bytes(b'intent,query_text,query_id\r\ni,A,q1\r\nj,B,q2\r\n')
        self.assertEqual(records_fingerprint(a,'queries')['semantic_sha256'],records_fingerprint(b,'queries')['semantic_sha256'])

    def test_meaning_change_changes_hash(self):
        a=self.root/'a.csv'; b=self.root/'b.csv'
        a.write_text('query_id,query_text,intent\nq1,A,i\n',encoding='utf-8')
        b.write_text('query_id,query_text,intent\nq1,Changed,i\n',encoding='utf-8')
        self.assertNotEqual(records_fingerprint(a,'queries')['semantic_sha256'],records_fingerprint(b,'queries')['semantic_sha256'])

    def test_source_hash_only_normalizes_newlines(self):
        a=self.root/'a.py'; b=self.root/'b.py'; c=self.root/'c.py'
        a.write_bytes(b'x=1\r\ny=2\r\n'); b.write_bytes(b'x=1\ny=2\n'); c.write_bytes(b'x=1\ny=3\n')
        self.assertEqual(source_fingerprint(a)['source_sha256'],source_fingerprint(b)['source_sha256'])
        self.assertNotEqual(source_fingerprint(a)['source_sha256'],source_fingerprint(c)['source_sha256'])

    def test_duplicate_keys_and_nonfinite_numbers_rejected(self):
        with self.assertRaises(ValueError): strict_json_text('{"a":1,"a":2}')
        with self.assertRaises(ValueError): strict_json_text('{"a":NaN}')

    def test_labels_change_only_evaluation_identity(self):
        retrieval=combine_fingerprint({'corpus':'c','query':'q','ranker':'r'})
        eval_a=combine_fingerprint({'retrieval':retrieval,'labels':'a'})
        eval_b=combine_fingerprint({'retrieval':retrieval,'labels':'b'})
        self.assertNotEqual(eval_a,eval_b)
        self.assertEqual(retrieval,combine_fingerprint({'corpus':'c','query':'q','ranker':'r'}))


if __name__ == '__main__': unittest.main()
