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


FORMAT_VERSION = "cevent-boss-die-semantic-json-1"
COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PC = 0x075F
FOLLOWER_SCRIPT_PC = 0x0910
SPAWN_AND_EFFECT_INT_PCS = {"follower_spawn_se_id": 0x0922, "initial_wait_frames": 0x0928, "post_effect_delay_frames": 0x0948, "post_effect_id": 0x094B, "post_effect_wait_frames": 0x0958, "final_cleanup_wait_frames": 0x0A1A}
SPAWN_AND_EFFECT_FLOAT_PCS = {"post_effect_scale": 0x0954, "final_cleanup_effect_scale": 0x0A22}
MOTION_INT_PCS = {"intro_motion_id": 0x0968, "intro_request_id": 0x096F, "main_motion_id": 0x0994, "main_replay_motion_id": 0x09AC, "request_motion_id": 0x09B2, "secondary_replay_motion_id": 0x09C7, "secondary_request_motion_id": 0x09CD}
MOTION_FLOAT_PCS = {"intro_motion_speed": 0x096A, "scale_start": 0x0986}
AUDIO_FLOAT_PCS = {"pitch_multiplier_primary": 0x099A, "pitch_multiplier_secondary": 0x09C3}


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


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	return {"format": FORMAT_VERSION, "source_basename": document.get("source_basename"),
		"embedded_scripts": {"linked_event_scripts": read_string_list(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS), "money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PC)[0], "follower_script": read_push_str(string_entries, pc_to_string_index, FOLLOWER_SCRIPT_PC)[0]},
		"spawn_and_effects": {"integers": {key: read_push_int(pc_to_instruction, pc) for key, pc in SPAWN_AND_EFFECT_INT_PCS.items()}, "floats": {key: read_push_float(pc_to_instruction, pc) for key, pc in SPAWN_AND_EFFECT_FLOAT_PCS.items()}},
		"motion_sequence": {"integers": {key: read_push_int(pc_to_instruction, pc) for key, pc in MOTION_INT_PCS.items()}, "floats": {key: read_push_float(pc_to_instruction, pc) for key, pc in MOTION_FLOAT_PCS.items()}},
		"audio_pitch": {key: read_push_float(pc_to_instruction, pc) for key, pc in AUDIO_FLOAT_PCS.items()},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["linked_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["money_item_script"]])
	write_push_str_group(string_entries, pc_to_string_index, [FOLLOWER_SCRIPT_PC], [semantic["embedded_scripts"]["follower_script"]])
	for key, pc in SPAWN_AND_EFFECT_INT_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["spawn_and_effects"]["integers"][key])
	for key, pc in SPAWN_AND_EFFECT_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["spawn_and_effects"]["floats"][key])
	for key, pc in MOTION_INT_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["motion_sequence"]["integers"][key])
	for key, pc in MOTION_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["motion_sequence"]["floats"][key])
	for key, pc in AUDIO_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["audio_pitch"][key])
	return document


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
	parser = argparse.ArgumentParser(description="Edit CEvent_boss_die.pkc via semantic JSON.")
	subparsers = parser.add_subparsers(dest="command", required=True)
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
