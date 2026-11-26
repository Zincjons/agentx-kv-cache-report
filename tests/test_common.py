import json
import tempfile
import unittest
from pathlib import Path
from experiments.common import join_day


class CommonJoinTests(unittest.TestCase):
    def test_time_disambiguates_users_and_uncertain_counts_abstain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / '2026-11-18' / 'Sage-S2.1-flash'
            (base / 'trace').mkdir(parents=True)
            (base / 'schedule').mkdir()
            def trow(t, tin, hashes, api, model='Sage'):
                return dict(t=t, type='n', model=model, **{'in':tin,'out':1},
                            hash_ids=hashes, api_time=float(api))
            groups = {
                '71-00000':[trow(0,80,[1,2],1),trow(1,13,[1],2)],
                '461-00000':[trow(0,13,[1],2)],
                '461-00001':[trow(0,70,[1,3],3)],
                '461-00002':[trow(0,70,[1,3],3)],
                '461-00003':[trow(0,3,[99],4)],
                '461-00004':[trow(0,3,[100],5,'Other')],
            }
            for group, rows in groups.items():
                (base/'trace'/f'{group}.json').write_text(json.dumps(
                    dict(id=group,block_size=64,requests=rows)))
            def srow(rid,ts,tin,hashes,api):
                return dict(request_id=rid,timestamp=ts,model='Sage',input_length=tin,
                            output_length=1,hash_ids=hashes,api_time=float(api),stream=False)
            rows=[srow('A',1000,80,[1,2],1),srow('B71',2000,13,[1],2),
                  srow('B461',5000,13,[1],2),srow('C1',6000,70,[1,3],3),
                  srow('C2',7000,70,[1,3],3),srow('D1',8000,3,[99],4),
                  srow('D2',9000,3,[99],4),srow('E',10000,3,[100],5)]
            (base/'schedule'/'part-00000.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            accepted, assignments, rejected, audit = join_day(root,'2026-11-18')
            by_id={r.row['request_id']:assignments[r.ordinal] for r in accepted}
            self.assertEqual(audit['strict_common_requests'],6)
            self.assertEqual(audit['accepted_requests'],5)
            self.assertEqual(audit['unresolved_common_requests'],1)
            self.assertEqual(by_id['B71']['uid'],'71')
            self.assertEqual(by_id['B461']['uid'],'461')
            self.assertEqual(by_id['C1']['trace_group'],'')
            self.assertEqual(by_id['C1']['uid'],'461')
            self.assertEqual(len(rejected),3)


if __name__ == '__main__':
    unittest.main()
