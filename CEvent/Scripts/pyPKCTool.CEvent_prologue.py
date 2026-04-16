#!/usr/bin/env python3
import argparse
import copy
import json
import os
import struct
import sys

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)


TOOL_DESCRIPTION = "Edit CEvent_prologue.pkc via semantic JSON."
FORMAT_VERSION = "cevent-prologue-semantic-json-1"

COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PC = 0x075F
DIRECTION_SUFFIX_PCS = {
	"center": 0x0901,
	"north": 0x0904,
	"east": 0x0907,
	"south": 0x090A,
	"west": 0x090D,
	"northeast": 0x0910,
	"southeast": 0x0913,
	"northwest": 0x0916,
	"southwest": 0x0919,
}
LAYOUT_FLOAT_PCS = {
	"text_scale_x": 0x093C,
	"text_scale_y": 0x093F,
	"play_speed": 0x0943,
	"request_next_speed": 0x094A,
}

INTRO_LAYOUT_PCS = {
	"bottom_archive_path": 0x098A,
	"bottom_layout_name": 0x098B,
	"full_archive_path": 0x098E,
	"full_layout_name": 0x098F,
	"bottom_play_motion": 0x0993,
	"full_play_motion": 0x0998,
	"event_name": 0x099C,
}
INTRO_SEQUENCE_A_PCS = [0x09A0, 0x09A4, 0x09A8, 0x09AC, 0x09B0, 0x09B4, 0x09B8, 0x09BC, 0x09C0, 0x09C4]
INTRO_SEQUENCE_B_PCS = [0x09C8, 0x09CC, 0x09D0, 0x09D4, 0x09D8, 0x09DC, 0x09E0, 0x09E4, 0x09E8, 0x09EC]
INTRO_CUE_GROUPS = [
	{"mid_pc": 0x09FE, "strings": [0x0A04, 0x0A05, 0x0A06], "duration_pc": 0x0A07, "se_pc": 0x09F9, "effect_id_pc": 0x0A0C, "effect_dir_pc": 0x0A0D},
	{"mid_pc": 0x0A16, "strings": [0x0A1C, 0x0A1D, 0x0A1E], "duration_pc": 0x0A22, "se_pc": 0x0A2B, "effect_id_pc": 0x0A2F, "effect_dir_pc": 0x0A30},
	{"mid_pc": 0x0A39, "strings": [0x0A3F, 0x0A40, 0x0A41], "duration_pc": 0x0A45},
	{"mid_pc": 0x0A50, "strings": [0x0A56, 0x0A57, 0x0A58], "duration_pc": 0x0A5C},
	{"mid_pc": 0x0A67, "strings": [0x0A6D, 0x0A6E, 0x0A6F], "duration_pc": 0x0A70},
	{"mid_pc": 0x0A7A, "strings": [0x0A80, 0x0A81, 0x0A82], "duration_pc": 0x0A86},
	{"mid_pc": 0x0A91, "strings": [0x0A97, 0x0A98, 0x0A99], "duration_pc": 0x0A9D},
]
GATE_SEQUENCE_STRING_PCS = {
	"event_name": 0x0AB4,
	"gate_model_brres": 0x0AB8,
	"gate_play_motion": 0x0AC0,
	"gate_next_motion": 0x0AC4,
	"camera_replay_motion": 0x0AC8,
	"camera_replay_next_motion": 0x0ACC,
	"helper_item_script_a": 0x0AD9,
	"koratta_model_brres": 0x0ADF,
	"koratta_play_motion": 0x0AEB,
	"koratta_next_motion": 0x0AEF,
	"helper_item_script_b": 0x0AF5,
	"warppoint_model_brres": 0x0AFB,
}
GATE_SEQUENCE_INT_PCS = {
	"gate_enable_bounding": 0x0ABC,
	"koratta_enable_bounding": 0x0AE3,
	"koratta_show_flag": 0x0AE7,
	"koratta_se_id": 0x0AF2,
	"warppoint_show_flag": 0x0AFF,
	"warp_fade_to_black_flag": 0x0B09,
	"warp_fade_to_black_frames": 0x0B0A,
	"spawn_effect_a_id": 0x0B11,
	"spawn_effect_a_dir_deg": 0x0B12,
	"spawn_effect_a_se_id": 0x0B15,
	"spawn_effect_b_id": 0x0B1C,
	"spawn_effect_b_dir_deg": 0x0B1D,
	"spawn_effect_b_se_id": 0x0B20,
	"extra_se_id_a": 0x0B23,
	"extra_se_id_b": 0x0B26,
	"show_hpbar_flag": 0x0B9C,
	"player_motion_id": 0x0BA8,
	"cleanup_fade_flag": 0x0BAB,
	"cleanup_fade_frames": 0x0BAC,
	"final_gc_id": 0x0BC0,
	"final_hold_frames": 0x0BC3,
	"final_destroy_target_flag": 0x0BC8,
}
GATE_SEQUENCE_INT_PCS_EXTRA = {
	"warppoint_position_x": 0x0B03,
	"warppoint_position_y": 0x0B04,
}
GATE_SEQUENCE_FLOAT_PCS = {
	"koratta_spawn_y_offset": 0x0AD4,
	"warppoint_position_z": 0x0B05,
	"player_setzx_x": 0x0BA0,
	"player_setzx_z": 0x0BA3,
}
REWARD_CUE_GROUPS = [
	{"mid_pc": 0x0B2E, "strings": [0x0B34, 0x0B35, 0x0B36], "duration_pc": 0x0B37, "hold_pc": 0x0B3B, "se_pc": 0x0B3E},
	{"mid_pc": 0x0B43, "strings": [0x0B49, 0x0B4A, 0x0B4B], "duration_pc": 0x0B4C, "hold_pc": 0x0B50, "se_pc": 0x0B53},
	{"mid_pc": 0x0B58, "strings": [0x0B5E, 0x0B5F, 0x0B60], "duration_pc": 0x0B61, "hold_pc": 0x0B65, "se_pc": 0x0B68},
	{"mid_pc": 0x0B6D, "strings": [0x0B73, 0x0B74, 0x0B75], "duration_pc": 0x0B76, "hold_pc": 0x0B7A},
	{"mid_pc": 0x0BB3, "strings": [0x0BB9, 0x0BBA, 0x0BBB], "duration_pc": 0x0BBC},
]

def _export_cue_groups(pc_to_instruction, pc_to_string_index, string_entries, groups):
	result = []
	for config in groups:
		entry = {
			"message_mid": read_push_int(pc_to_instruction, config["mid_pc"]),
			"begin_motion": read_push_str(string_entries, pc_to_string_index, config["strings"][0])[0],
			"loop_motion": read_push_str(string_entries, pc_to_string_index, config["strings"][1])[0],
			"end_motion": read_push_str(string_entries, pc_to_string_index, config["strings"][2])[0],
			"duration_seconds": read_push_float(pc_to_instruction, config["duration_pc"]),
		}
		if "hold_pc" in config:
			entry["hold_frames"] = read_push_int(pc_to_instruction, config["hold_pc"])
		if "se_pc" in config:
			entry["se_id"] = read_push_int(pc_to_instruction, config["se_pc"])
		if "effect_id_pc" in config:
			entry["effect_id"] = read_push_int(pc_to_instruction, config["effect_id_pc"])
		if "effect_dir_pc" in config:
			entry["effect_dir_deg"] = read_push_int(pc_to_instruction, config["effect_dir_pc"])
		result.append(entry)
	return result


def _apply_cue_groups(pc_to_instruction, pc_to_string_index, string_entries, groups, values, label):
	if len(values) != len(groups):
		raise ValueError(f"{label} must contain {len(groups)} entries")
	for config, entry in zip(groups, values):
		write_push_int(pc_to_instruction, config["mid_pc"], entry["message_mid"])
		write_push_str_group(string_entries, pc_to_string_index, config["strings"], [entry["begin_motion"], entry["loop_motion"], entry["end_motion"]])
		write_push_float(pc_to_instruction, config["duration_pc"], entry["duration_seconds"])
		if "hold_pc" in config:
			write_push_int(pc_to_instruction, config["hold_pc"], entry["hold_frames"])
		if "se_pc" in config:
			write_push_int(pc_to_instruction, config["se_pc"], entry["se_id"])
		if "effect_id_pc" in config:
			write_push_int(pc_to_instruction, config["effect_id_pc"], entry["effect_id"])
		if "effect_dir_pc" in config:
			write_push_int(pc_to_instruction, config["effect_dir_pc"], entry["effect_dir_deg"])


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"embedded_scripts": {
			"linked_event_scripts": read_string_list(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS),
			"money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PC)[0],
		},
		"direction_suffixes": {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in DIRECTION_SUFFIX_PCS.items()},
		"layout_animation": {key: read_push_float(pc_to_instruction, pc) for key, pc in LAYOUT_FLOAT_PCS.items()},
		"intro_layouts": {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in INTRO_LAYOUT_PCS.items()},
		"intro_sequences": {
			"primary": read_string_list(string_entries, pc_to_string_index, INTRO_SEQUENCE_A_PCS),
			"secondary": read_string_list(string_entries, pc_to_string_index, INTRO_SEQUENCE_B_PCS),
			"announcement_cues": _export_cue_groups(pc_to_instruction, pc_to_string_index, string_entries, INTRO_CUE_GROUPS),
		},
		"gate_sequence": {
			"strings": {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in GATE_SEQUENCE_STRING_PCS.items()},
			"integers": ({key: read_push_int(pc_to_instruction, pc) for key, pc in GATE_SEQUENCE_INT_PCS.items()} | {key: read_push_int(pc_to_instruction, pc) for key, pc in GATE_SEQUENCE_INT_PCS_EXTRA.items()}),
			"floats": {key: read_push_float(pc_to_instruction, pc) for key, pc in GATE_SEQUENCE_FLOAT_PCS.items()},
			"reward_cues": _export_cue_groups(pc_to_instruction, pc_to_string_index, string_entries, REWARD_CUE_GROUPS),
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["linked_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["money_item_script"]])
	for key, pc in DIRECTION_SUFFIX_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["direction_suffixes"][key]])
	for key, pc in LAYOUT_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["layout_animation"][key])
	for key, pc in INTRO_LAYOUT_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["intro_layouts"][key]])
	write_push_str_group(string_entries, pc_to_string_index, INTRO_SEQUENCE_A_PCS, semantic["intro_sequences"]["primary"])
	write_push_str_group(string_entries, pc_to_string_index, INTRO_SEQUENCE_B_PCS, semantic["intro_sequences"]["secondary"])
	_apply_cue_groups(pc_to_instruction, pc_to_string_index, string_entries, INTRO_CUE_GROUPS, semantic["intro_sequences"]["announcement_cues"], "intro_sequences.announcement_cues")
	for key, pc in GATE_SEQUENCE_STRING_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["gate_sequence"]["strings"][key]])
	for key, pc in GATE_SEQUENCE_INT_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["gate_sequence"]["integers"][key])
	for key, pc in GATE_SEQUENCE_INT_PCS_EXTRA.items():
		write_push_int(pc_to_instruction, pc, semantic["gate_sequence"]["integers"][key])
	for key, pc in GATE_SEQUENCE_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["gate_sequence"]["floats"][key])
	_apply_cue_groups(pc_to_instruction, pc_to_string_index, string_entries, REWARD_CUE_GROUPS, semantic["gate_sequence"]["reward_cues"], "gate_sequence.reward_cues")
	return document


def command_summary(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	print(f"Intro event: {semantic['intro_layouts']['event_name']}")
	print(f"Primary intro motions: {len(semantic['intro_sequences']['primary'])}")
	print(f"Gate event: {semantic['gate_sequence']['strings']['event_name']}")
	print(f"Reward cues: {len(semantic['gate_sequence']['reward_cues'])}")

def dump_json(path, document):
	with open(path, "w", encoding="utf-8", newline="\n") as outfile:
		outfile.write(json.dumps(document, ensure_ascii=False, indent="\t"))
		outfile.write("\n")


def load_json(path):
	with open(path, "r", encoding="utf-8") as infile:
		return json.load(infile)


def basename_without_ext(path):
	return os.path.splitext(os.path.basename(path))[0]


def default_json_output(input_path):
	return basename_without_ext(input_path) + ".json"


def default_pkc_output(original_pkc_path):
	base, ext = os.path.splitext(original_pkc_path)
	return base + ".rebuilt" + ext


def signed24_to_u24(value):
	integer = int(value)
	if not -(1 << 23) <= integer < (1 << 23):
		raise ValueError(f"Value out of signed 24-bit range: {integer}")
	return integer & 0xFFFFFF


def build_pc_maps(document):
	pc_to_instruction = {}
	pc_to_string_index = {}
	for instruction in document["code_section"]["instructions"]:
		pc = int(instruction["pc_units"])
		pc_to_instruction[pc] = instruction
		if instruction["opname"] == "push_str":
			pc_to_string_index[pc] = int(instruction.get("decoded", {}).get("index"))
	return pc_to_instruction, pc_to_string_index


def expect_instruction(pc_to_instruction, pc, opname):
	instruction = pc_to_instruction.get(pc)
	if instruction is None:
		raise ValueError(f"Expected instruction at PC 0x{pc:04X}, but none was found")
	if instruction["opname"] != opname:
		raise ValueError(f"Expected {opname} at PC 0x{pc:04X}, found {instruction['opname']}")
	return instruction


def read_push_int(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	return int(instruction.get("decoded", {}).get("value_signed"))


def write_push_int(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	instruction["imm"] = signed24_to_u24(value)
	instruction["imm_hex"] = f"0x{instruction['imm']:06X}"
	instruction["decoded"] = {"type": "int24", "value_signed": int(value), "value_unsigned": instruction["imm"]}


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	return float(instruction.get("decoded", {}).get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(value)
	instruction["payload_hex"] = struct.pack(">f", float_value).hex().upper()
	instruction["decoded"] = {"type": "float", "value": float_value}


def read_push_str(string_entries, pc_to_string_index, pc):
	if pc not in pc_to_string_index:
		raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
	index = pc_to_string_index[pc]
	return string_entries[index], index


def write_push_str_group(string_entries, pc_to_string_index, pcs, values):
	index_to_value = {}
	for pc, value in zip(pcs, values):
		if pc not in pc_to_string_index:
			raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
		index = pc_to_string_index[pc]
		if index in index_to_value and index_to_value[index] != value:
			raise ValueError(f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {value!r}")
		index_to_value[index] = value
	for index, value in index_to_value.items():
		string_entries[index] = str(value)


def read_string_list(string_entries, pc_to_string_index, pcs):
	return [read_push_str(string_entries, pc_to_string_index, pc)[0] for pc in pcs]


def read_int_list(pc_to_instruction, pcs):
	return [read_push_int(pc_to_instruction, pc) for pc in pcs]


def read_float_list(pc_to_instruction, pcs):
	return [read_push_float(pc_to_instruction, pc) for pc in pcs]


def write_int_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_int(pc_to_instruction, pc, value)


def write_float_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_float(pc_to_instruction, pc, value)


def command_export(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	output = args.output or default_json_output(args.input)
	dump_json(output, semantic)
	print(f"Wrote {output}")


def command_import(args):
	semantic = load_json(args.input)
	document = decode_pkc(args.original_pkc)
	document = apply_semantic(document, semantic)
	output = args.output or default_pkc_output(args.original_pkc)
	with open(output, "wb") as outfile:
		outfile.write(encode_pkc_document(document))
	print(f"Wrote {output}")


def command_verify(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	rebuilt_document = apply_semantic(copy.deepcopy(document), semantic)
	rebuilt = encode_pkc_document(rebuilt_document)
	with open(args.input, "rb") as infile:
		original = infile.read()
	if rebuilt != original:
		print("Roundtrip FAILED", file=sys.stderr)
		for index, (left, right) in enumerate(zip(original, rebuilt)):
			if left != right:
				print(f"First difference at 0x{index:X}: original=0x{left:02X}, rebuilt=0x{right:02X}", file=sys.stderr)
				break
		if len(original) != len(rebuilt):
			print(f"Length differs: original={len(original)} rebuilt={len(rebuilt)}", file=sys.stderr)
		raise SystemExit(1)
	print("Roundtrip OK")


def build_parser():
	parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
	subparsers = parser.add_subparsers(dest="command", required=True)
	summary_parser = subparsers.add_parser("summary", help="Show a short semantic summary.")
	summary_parser.add_argument("input", help="Input PKC file")
	summary_parser.set_defaults(func=command_summary)
	export_parser = subparsers.add_parser("export", help="Export a PKC to editable JSON.")
	export_parser.add_argument("input", help="Input PKC file")
	export_parser.add_argument("output", nargs="?", help="Output JSON file")
	export_parser.set_defaults(func=command_export)
	import_parser = subparsers.add_parser("import", help="Rebuild a PKC from exported JSON.")
	import_parser.add_argument("input", help="Input JSON file")
	import_parser.add_argument("original_pkc", help="Original PKC file used for default rebuilt naming")
	import_parser.add_argument("output", nargs="?", help="Output PKC file")
	import_parser.set_defaults(func=command_import)
	verify_parser = subparsers.add_parser("verify", help="Decode and immediately rebuild a PKC, then compare bytes.")
	verify_parser.add_argument("input", help="Input PKC file")
	verify_parser.set_defaults(func=command_verify)
	return parser


def main():
	parser = build_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
