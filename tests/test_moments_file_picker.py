"""WeChat 4.1.13's observed Select File dialog; no UI or publish actions."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
import moments_ui as ui


class FilePickerTests(unittest.TestCase):
    def dialog(self,name='Select File',states=None):
        return {'role':'dialog','name':name,'states':['ACTIVE'] if states is None else states,
                'bounds':{'x':649,'y':20,'width':630,'height':415},'children':[
                    {'role':'text','name':'File name:','states':['EDITABLE','FOCUSED'],
                     'bounds':{'x':749,'y':368,'width':433,'height':25}},
                    {'role':'push-button','name':'Open','states':[],
                     'bounds':{'x':1188,'y':368,'width':80,'height':25}},
                    {'role':'push-button','name':'Cancel','states':[],
                     'bounds':{'x':1188,'y':399,'width':80,'height':25}}]}

    def test_observed_select_file_title_is_recognized(self):
        dialog=self.dialog()
        self.assertIs(ui.image_file_dialog({'children':[dialog]}),dialog)

    def test_hidden_or_defunct_select_file_does_not_compete_with_visible_open(self):
        for state in ('DEFUNCT','INVISIBLE','HIDDEN'):
            with self.subTest(state=state):
                stale=self.dialog(states=['ACTIVE',state]);current=self.dialog('Open')
                self.assertIs(ui.image_file_dialog({'children':[stale,current]}),current)

    def test_two_active_dialogs_remain_ambiguous_and_never_click(self):
        with patch.object(ui,'click_node') as click:
            with self.assertRaisesRegex(ValueError,'not unique'):
                ui.image_file_dialog({'children':[self.dialog(),self.dialog('Open')]})
        click.assert_not_called()

    def test_native_window_lookup_and_file_field_use_the_same_select_file_dialog(self):
        dialog=self.dialog();commands=[];selected=[False]
        editor={'role':'text','name':'','states':['EDITABLE'],'bounds':{'width':300,'height':60}}
        composer={'role':'frame','name':'Moments','children':[editor,{'role':'push-button','name':'Cancel'}]}
        def command(*args,**kwargs):
            commands.append(args)
            output=b'1280 800\n' if args[1]=='getdisplaygeometry' else b'4321\n' if args[1]=='search' else b''
            return type('Result',(),{'stdout':output})()
        with patch.object(ui,'tree',side_effect=lambda:composer if selected[0] else {'children':[dialog]}), \
             patch.object(ui,'command',side_effect=command), \
             patch.object(ui,'put_text') as put,patch.object(ui,'click_node',side_effect=lambda _:selected.__setitem__(0,True)) as click:
            ui.select_publish_images(['/data/moments-publish-assets/test.png'])
        search=next(c for c in commands if c[1]=='search')
        self.assertIn('Select File',search[-1])
        put.assert_called_once_with(dialog['children'][0],'"/data/moments-publish-assets/test.png"')
        click.assert_called_once_with(dialog['children'][1])


if __name__=='__main__':
    unittest.main()
