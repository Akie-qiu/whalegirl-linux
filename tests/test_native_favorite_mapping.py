"""Pure index mapping; never clicks or sends a WeChat message."""
import ast
from pathlib import Path
import unittest

source=Path(__file__).resolve().parents[1]/'deploy/chat_select_linux.py'
node=next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name=='favorite_tile')
namespace={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
favorite_tile=namespace['favorite_tile']


class FavoriteMappingTests(unittest.TestCase):
    def test_full_grid_then_virtual_page_keeps_absolute_identity(self):
        tiles=[{'native_ref':str(i)} for i in range(56)];known={}
        self.assertEqual(favorite_tile(tiles,40,56,known),tiles[40])
        page=tiles[26:]
        self.assertEqual(favorite_tile(page,40,56,known),tiles[40])
        self.assertEqual(known['40'],40)

    def test_missing_or_conflicting_anchors_never_guess_a_tile(self):
        tiles=[{'native_ref':'a'},{'native_ref':'b'},{'native_ref':'c'}]
        self.assertIsNone(favorite_tile(tiles,2,20,{}))
        self.assertIsNone(favorite_tile(tiles,2,20,{'a':1,'b':7}))

    def test_conflicting_offsets_refuse_even_if_target_anchor_exists(self):
        tiles=[{'native_ref':'a'},{'native_ref':'target'},{'native_ref':'c'}]
        self.assertIsNone(favorite_tile(tiles,5,20,{'target':5,'a':1,'c':7}))

    def test_middle_favorite_can_be_reached_from_the_nearer_bottom_boundary(self):
        tiles=[{'native_ref':str(i)} for i in range(67)]
        known={str(i):i for i in range(45,67)}
        self.assertEqual(favorite_tile(tiles[40:],43,67,known),tiles[43])


if __name__=='__main__':unittest.main()
