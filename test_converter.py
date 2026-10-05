"""Lancer : python -m unittest -v. Aucune donnée client dans ces tests."""
import copy
import unittest
from app import FIELDS, balance, bundle, csv_bytes, export_fec, make_groups, money, parse_file


def row(identifier, debit=0, credit=0, date='20220101', piece='X', account='411ABC'):
    r = dict.fromkeys(FIELDS, '')
    r.update(JournalCode='BQ', JournalLib='Banque', EcritureDate=date, CompteNum=account,
             CompteLib='Compte', PieceRef=piece, EcritureLib='Libellé', Debit=str(debit), Credit=str(credit))
    r.update(_id=identifier, _debit=debit, _credit=credit)
    r['_original'] = dict(r)
    return r


class ConverterTests(unittest.TestCase):
    def test_exact_cents_and_invalid(self):
        self.assertEqual(money('1 234,56000000'), 123456)
        for bad in ['1,234', 'NaN', 'inf', '1.234,56', 'abc']:
            with self.assertRaises(ValueError):
                money(bad)

    def test_sequence_missing_piece_and_trace(self):
        rows = [row(2, credit=200, piece='CLIENT1'), row(3, credit=300, piece='CLIENT2'), row(4, debit=500, piece='', account='512000')]
        groups = make_groups(rows)
        self.assertEqual(len(groups), 1)
        out, audit, additions = export_fec(groups)
        self.assertEqual(len(out), 3)
        self.assertEqual(len({x['PieceRef'] for x in out}), 1)
        self.assertIn('CLIENT1', out[0]['EcritureLib'])
        self.assertEqual(out[0]['CompteNum'], '411ABC')
        self.assertEqual(len(audit), 3)
        self.assertFalse(additions)

    def test_date_boundaries_and_suspense(self):
        rows = [row(2, debit=100), row(3, credit=100, date='20220102')]
        groups = make_groups(rows)
        self.assertEqual(len(groups), 2)
        with self.assertRaises(ValueError):
            export_fec(groups)
        out, audit, additions = export_fec(groups, suspense='471999')
        self.assertEqual(len(out), 4)
        self.assertEqual(len(additions), 2)
        self.assertEqual(sum(money(r['Debit']) - money(r['Credit']) for r in additions), 0)
        self.assertEqual({r['EcritureDate'] for r in out}, {'20220101', '20220102'})

    def test_global_imbalance_never_hidden(self):
        with self.assertRaises(ValueError):
            export_fec(make_groups([row(2, debit=100)]), suspense='471999')

    def test_existing_suspense_never_mixed(self):
        with self.assertRaises(ValueError):
            export_fec(make_groups([row(2, debit=100, account='471999'), row(3, credit=100)]), suspense='471999')

    def test_lettering_removes_both_fields_target_only(self):
        rows = [row(2, debit=100), row(3, credit=100, account='512000')]
        for r in rows:
            r['EcritureLet'], r['DateLet'] = 'AAA', '20220201'
        out, _, _ = export_fec(make_groups(rows), remove_accounts=['512000'])
        self.assertEqual((out[0]['EcritureLet'], out[0]['DateLet']), ('AAA', '20220201'))
        self.assertEqual((out[1]['EcritureLet'], out[1]['DateLet']), ('', ''))

    def test_fec_multidate_blocked(self):
        rows = [row(2, debit=100), row(3, credit=100, date='20220102')]
        for r in rows:
            r['EcritureNum'] = '1'
        with self.assertRaises(ValueError):
            export_fec(make_groups(rows, is_fec=True))

    def test_ebp_parse_encoding_and_no_silent_loss(self):
        text = 'Code journal;Date;N° de compte;Débit;Crédit\nBQ;01/01/2022;411ABC;10,00000000;0\nBQ;01/01/2022;512000;0;10\n'
        r, meta = parse_file(text.encode('cp1252'))
        self.assertEqual(len(r), 2)
        self.assertEqual(balance(r), 0)
        self.assertEqual(meta['format'], 'EBP')
        with self.assertRaises(ValueError):
            parse_file((text + 'BQ;INVALID;411ABC;0;10\n').encode('cp1252'))
        with self.assertRaises(ValueError):
            parse_file((text + 'BQ;01/01/2022;411ABC;0;10;EXTRA\n').encode('cp1252'))

    def test_duplicates_preserved_and_fec_roundtrip(self):
        r = [row(2, debit=100), row(3, credit=100), row(4, debit=100), row(5, credit=100)]
        out, _, _ = export_fec(make_groups(r))
        self.assertEqual(len(out), 4)
        parsed, meta = parse_file(csv_bytes(out, FIELDS, '\t'))
        self.assertEqual(len(parsed), 4)
        self.assertEqual(balance(parsed), 0)
        self.assertTrue(all(not x['ValidDate'] for x in parsed))

    def test_opening_preserves_historic_refs_and_dates(self):
        r = [row(2, debit=100, piece='A'), row(3, credit=100, piece='B')]
        r[0]['PieceDate'] = '20201231'
        out, _, _ = export_fec(make_groups(r, opening=['BQ']))
        self.assertEqual(out[0]['PieceDate'], '20201231')
        self.assertIn('Réf. source : A', out[0]['EcritureLib'])


if __name__ == '__main__':
    unittest.main()
