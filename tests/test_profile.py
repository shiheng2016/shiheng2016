import os
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import update_profile as p


class ProfileTests(unittest.TestCase):
    def fixture(self):
        return dict(profile=dict(created_at='2016-04-23T12:42:51Z',location='ShangHai,China',
                                 email='shiweiping0828@gmail.com',blog='https://www.yuque.com/allenstone',
                                 twitter_username='VPingShi'), languages=['JavaScript', 'HTML', 'CSS'],
                    repos=47,contributed=0,stars=41,commits=1234,followers=17,
                    additions=1200000,deletions=1100000,scope='public',updated='2026-11-03 05:00 UTC')

    def test_geometry_and_themes(self):
        for theme in p.PALETTES:
            root=ET.fromstring(p.render(self.fixture(),theme))
            self.assertEqual(root.attrib['viewBox'],'0 0 840 500')
            texts=root.findall('{http://www.w3.org/2000/svg}text')
            info=[n for n in texts if n.attrib['x']=='390']
            self.assertLessEqual(max(float(n.attrib['y']) for n in info),485)
            for row in info:
                self.assertLessEqual(len(''.join(row.itertext())),56)
            self.assertIn(p.PALETTES[theme]['bg'],p.render(self.fixture(),theme))

    def test_escape(self):
        stats=self.fixture()
        stats['profile']['location']='A&B <city>'
        xml=p.render(stats,'dark')
        self.assertIn('A&amp;B &lt;city&gt;',xml)
        ET.fromstring(xml)

    def test_public_scope_even_with_pat(self):
        with patch.dict(os.environ,{'GITHUB_TOKEN':'not-a-real-token'},clear=True):
            self.assertEqual(p.repo_scope(),', privacy: PUBLIC')
        with patch.dict(os.environ,{'ACCESS_TOKEN':'not-a-real-token'},clear=True):
            self.assertEqual(p.repo_scope(),'')

    def test_public_year_filters_private_contributions(self):
        response={'user':{'contributionsCollection':dict(totalCommitContributions=999,
            restrictedContributionsCount=999,commitContributionsByRepository=[
                {'repository':{'isPrivate':False},'contributions':{'totalCount':12}},
                {'repository':{'isPrivate':True},'contributions':{'totalCount':50}}])}}
        with patch.dict(os.environ,{},clear=True),patch.object(p,'graphql',return_value=response):
            self.assertEqual(p.yearly_commits(2020,p.datetime.now(p.timezone.utc)),12)

    def test_history_pagination(self):
        def page(a,d,next_page):
            return {'repository':{'defaultBranchRef':{'target':{'history':{
                'nodes':[{'oid':'a','additions':a,'deletions':d}],
                'pageInfo':{'hasNextPage':next_page,'endCursor':'next'}}}}}}
        with patch.object(p,'graphql',side_effect=[page(10,2,True),page(20,4,False)]) as api:
            self.assertEqual(p.repository_lines({'nameWithOwner':'owner/repo'},'id'),(30,6,2))
            self.assertEqual(api.call_args.kwargs['cursor'],'next')

    def test_art_dimensions_and_readme(self):
        lines=p.ART.strip('\n').splitlines()
        self.assertLessEqual(max(map(len,lines)),46)
        self.assertNotIn('__AVATAR_PENDING__',p.ART)
        self.assertEqual(len((p.ROOT/'README.md').read_text().splitlines()),5)

    def test_cjk_width(self):
        stats=self.fixture()
        stats['profile']['location']='上'*30
        xml=p.render(stats,'dark')
        root=ET.fromstring(xml)
        for row in root.findall('{http://www.w3.org/2000/svg}text'):
            if row.attrib['x']=='390':
                self.assertLessEqual(p.cell_width(''.join(row.itertext())),56)
        self.assertIn('...',xml)

    def test_avatar_reproducibility(self):
        from tools.ascii_avatar import decode_png, render_ascii
        width,height,pixels=decode_png(p.ROOT/'img/avatar.png')
        art=render_ascii(width,height,pixels,background='portrait-wall')
        self.assertEqual(art,(p.ROOT/'tools/avatar.txt').read_text())
        self.assertEqual(art.rstrip('\n'),p.ART.strip('\n'))

    def test_graphql_rate_limit_retries(self):
        import io
        from urllib.error import HTTPError
        def response(data):
            return io.StringIO(p.json.dumps(data))
        with patch.dict(os.environ,{'GITHUB_TOKEN':'fake'},clear=True), \
             patch.object(p,'urlopen',side_effect=[response({'errors':[{'type':'RATE_LIMITED'}]}),response({'data':{}})]), \
             patch.object(p.time,'sleep') as sleep:
            self.assertEqual(p.api('graphql',{}),{'data':{}})
            sleep.assert_called_once()
        error=HTTPError('url',403,'limited',{'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'1090'},None)
        with patch.dict(os.environ,{'GITHUB_TOKEN':'fake'},clear=True), \
             patch.object(p,'urlopen',side_effect=[error,response({'ok':True})]), \
             patch.object(p.time,'time',return_value=1000),patch.object(p.time,'sleep') as sleep:
            p.api('graphql',{})
            self.assertGreaterEqual(sleep.call_args.args[0],90)

    def test_field_overflow_is_explicit(self):
        with self.assertRaises(ValueError):
            p.dotted('Email','x'*70)


if __name__=='__main__':
    unittest.main()
