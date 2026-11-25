import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from transfer_jp_voice_resources import is_voice_clip, select_jp_clip  # noqa: E402


class VoiceFilterTests(unittest.TestCase):
    def test_existing_voice_names_still_match(self):
        self.assertTrue(is_voice_clip("Vo_NPC_Nao_Angry_01", ""))
        self.assertTrue(is_voice_clip("Q_Main_G1_C1_0170_10_KiddingTar_Math_VO_01", ""))
        self.assertTrue(is_voice_clip("Anything", "assets/sound/npc/voice/x.wav"))

    def test_numbered_lines_and_combat_vocals_match(self):
        self.assertTrue(is_voice_clip("Q_Opening_A1_40_PortalBreakdown_NaoZWhite_VO1", ""))
        self.assertTrue(is_voice_clip("SFX_Succubus_Sword_Attack1_vox", ""))
        self.assertTrue(is_voice_clip("SFX_HighPriest_AbyssS2_Attack_Vox_A", ""))
        self.assertTrue(is_voice_clip("wav_Morgant_Charging_Low_Vox2", ""))
        self.assertTrue(is_voice_clip("SFX_MagicGrenade_StrangeCat_Ground_Voice", ""))

    def test_sung_bgm_exception_matches_only_that_track(self):
        self.assertTrue(is_voice_clip("BGM_Sub_Quest_LuaSong", "assets/patchableassets/sound/bgm/bgm_sub_quest_luasong.wav"))
        self.assertFalse(is_voice_clip("BGM_Sub_Quest_Other", "assets/patchableassets/sound/bgm/bgm_sub_quest_other.wav"))

    def test_snl_lua_song_is_selected_and_takes_the_jp_vocal_track(self):
        snl = ("BGM_SNL_Sub_Quest_LuaSong", "assets/patchableassets/sound/audioclips/snl/bgm_snl_sub_quest_luasong.wav")
        main = ("BGM_Sub_Quest_LuaSong", "assets/patchableassets/sound/bgm/bgm_sub_quest_luasong.wav")
        self.assertTrue(is_voice_clip(*snl))
        # JP also ships the SNL copy with the TW vocals; the alias must win over it.
        by_full = {(snl[0].casefold(), snl[1]): "jp-snl", (main[0].casefold(), main[1]): "jp-main"}
        by_name = {snl[0].casefold(): ["jp-snl"], main[0].casefold(): ["jp-main"]}
        self.assertEqual(select_jp_clip(snl[0], snl[1], by_full, by_name), ("jp-main", "alias"))
        self.assertEqual(select_jp_clip(main[0], main[1], by_full, by_name), ("jp-main", "name+container"))
        self.assertEqual(select_jp_clip(snl[0], snl[1], {}, {}), (None, "missing-or-ambiguous"))

    def test_words_that_only_start_with_vo_do_not_match(self):
        for name in ("SFX_VoteResult", "SFX_VoidCut_01", "SFX_Vocal_Test", "BGM_Volcano", "SFX_Voxel"):
            self.assertFalse(is_voice_clip(name, "assets/sound/sfx/x.wav"), name)


if __name__ == "__main__":
    unittest.main()
