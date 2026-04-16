
#!/usr/bin/env python3
import argparse
import json
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	import pyPKCToolBase as generic
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)

PUSH_INT = generic.PKCOpCode.push_int.value
PUSH_FLOAT = generic.PKCOpCode.push_s_f.value
PUSH_STR = generic.PKCOpCode.push_str.value
CALL_IMM = generic.PKCOpCode.call_imm.value
POP = generic.PKCOpCode.pop.value


def dump_json(path, document):
	with open(path, "w", encoding="utf-8", newline="\n") as outfile:
		outfile.write(json.dumps(document, ensure_ascii=False, indent="	"))
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


def build_pc_maps(document):
	pc_to_instruction = {}
	pc_to_string_index = {}
	for instruction in document["code_section"]["instructions"]:
		pc = int(instruction["pc_units"])
		pc_to_instruction[pc] = instruction
		if instruction["opname"] == "push_str":
			decoded = instruction.get("decoded", {})
			pc_to_string_index[pc] = int(decoded["index"])
	return pc_to_instruction, pc_to_string_index


def expect_instruction(pc_to_instruction, pc, opname):
	instruction = pc_to_instruction.get(pc)
	if instruction is None:
		raise ValueError(f"Expected instruction at PC 0x{pc:04X}, but none was found")
	if instruction["opname"] != opname:
		raise ValueError(f"Expected {opname} at PC 0x{pc:04X}, found {instruction['opname']}")
	return instruction


def signed24_to_u24(value):
	integer = int(value)
	if not -(1 << 23) <= integer < (1 << 23):
		raise ValueError(f"Value out of signed 24-bit range: {integer}")
	return integer & 0xFFFFFF


def read_push_int(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	return int(instruction.get("decoded", {}).get("value_signed"))


def write_push_int(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	instruction["imm"] = signed24_to_u24(value)
	instruction["imm_hex"] = f"0x{instruction['imm']:06X}"
	instruction["decoded"] = {
		"type": "int24",
		"value_signed": int(value),
		"value_unsigned": instruction["imm"],
	}


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	return float(instruction.get("decoded", {}).get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(value)
	instruction["payload_hex"] = struct.pack(">f", float_value).hex().upper()
	instruction["decoded"] = {
		"type": "float",
		"value": float_value,
	}


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
			raise ValueError(
				f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {value!r}"
			)
		index_to_value[index] = str(value)
	for index, value in index_to_value.items():
		string_entries[index] = value


def read_string_list(string_entries, pc_to_string_index, pcs):
	return [read_push_str(string_entries, pc_to_string_index, pc)[0] for pc in pcs]


def read_int_list(pc_to_instruction, pcs):
	return [read_push_int(pc_to_instruction, pc) for pc in pcs]


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


def load_document(pkc_path):
	return generic.decode_pkc(pkc_path)


def save_document(path, document):
	raw = generic.encode_pkc_document(document)
	with open(path, "wb") as outfile:
		outfile.write(raw)


def command_verify_impl(args, export_semantic):
	document = load_document(args.pkc_path)
	semantic = export_semantic(document)
	rebuilt_document = load_document(args.pkc_path)
	apply_semantic(rebuilt_document, semantic)
	with open(args.pkc_path, "rb") as infile:
		original_raw = infile.read()
	rebuilt_raw = generic.encode_pkc_document(rebuilt_document)
	matches = rebuilt_raw == original_raw
	print(f"Match: {'YES' if matches else 'NO'}")
	if not matches:
		for index, (left, right) in enumerate(zip(rebuilt_raw, original_raw)):
			if left != right:
				print(f"First mismatch at 0x{index:X}: rebuilt=0x{left:02X}, original=0x{right:02X}")
				break
		if len(rebuilt_raw) != len(original_raw):
			print(f"Length mismatch: rebuilt={len(rebuilt_raw)}, original={len(original_raw)}")
		raise SystemExit(1)


def command_export_impl(args, export_semantic):
	document = load_document(args.pkc_path)
	semantic = export_semantic(document)
	output_path = args.output or default_json_output(args.pkc_path)
	dump_json(output_path, semantic)
	print(f"Exported {output_path}")


def command_import_impl(args, apply_semantic):
	document = load_document(args.original_pkc)
	semantic = load_json(args.json_path)
	apply_semantic(document, semantic)
	output_path = args.output_pkc or default_pkc_output(args.original_pkc)
	save_document(output_path, document)
	print(f"Wrote {output_path}")

FORMAT_VERSION = "cgameending-semantic-json-1"
COMMON_EVENT_SCRIPT_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PC = 0x075F
OPENING_PCS = {
	"bgm_domestic": 0x0905,
	"bgm_international": 0x0909,
	"event_name": 0x0913,
	"model_brres": 0x091A,
	"motion_open": 0x091E,
	"motion_request": 0x0922,
	"widescreen_move": 0x0938,
	"widescreen_loop": 0x093C,
	"normal_move": 0x0941,
	"normal_loop": 0x0945,
	"placement_event": 0x098D,
	"placement_group": 0x098E,
	"placement_target": 0x098F,
	"placement_species_min": 0x09A1,
	"placement_species_max": 0x09A2,
}
STAFF_ROLL_TEXT_ID_PCS = {
	"domestic": [0x09D2, 0x09D7, 0x09DC, 0x09E1],
	"international": [0x09E7, 0x09EC, 0x09F1, 0x09F6, 0x09FB, 0x0A00, 0x0A05],
}
CHECK_AND_PROMPT_PCS = {
	"required_flag": 0x0A12,
	"cancelled_label": 0x0A1A,
}
EPILOGUE_PCS = {
	"follower_script": 0x0ABF,
	"event_name": 0x0AC4,
	"main_model_brres": 0x0ACB,
	"main_motion": 0x0ACF,
	"neji_model_brres": 0x0AE2,
	"neji_motion": 0x0AE6,
	"null_animation_brres": 0x0B1E,
	"null_target_motion": 0x0B26,
	"ground_motion_primary": 0x0B2A,
	"ground_motion_secondary": 0x0B2E,
	"face_target_node": 0x0B4C,
}
EPILOGUE_INT_PCS = {
	"epilogue_bgm": 0x0B35,
	"epilogue_start_se": 0x0B39,
	"effect_id_a": 0x0B6C,
	"effect_id_b": 0x0B70,
}
EPILOGUE_SEQUENCE_WAIT_PCS = [0x0B40, 0x0B47, 0x0B53, 0x0B5A, 0x0B61, 0x0B68]


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	opening = {
		"bgm_domestic": read_push_int(pc_to_instruction, OPENING_PCS["bgm_domestic"]),
		"bgm_international": read_push_int(pc_to_instruction, OPENING_PCS["bgm_international"]),
		"event_name": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["event_name"])[0],
		"model_brres": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["model_brres"])[0],
		"motion_open": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["motion_open"])[0],
		"motion_request": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["motion_request"])[0],
		"widescreen_move": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["widescreen_move"])[0],
		"widescreen_loop": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["widescreen_loop"])[0],
		"normal_move": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["normal_move"])[0],
		"normal_loop": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["normal_loop"])[0],
		"placement_event": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["placement_event"])[0],
		"placement_group": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["placement_group"])[0],
		"placement_target": read_push_str(string_entries, pc_to_string_index, OPENING_PCS["placement_target"])[0],
		"placement_species_min": read_push_int(pc_to_instruction, OPENING_PCS["placement_species_min"]),
		"placement_species_max": read_push_int(pc_to_instruction, OPENING_PCS["placement_species_max"]),
	}
	epilogue = {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in EPILOGUE_PCS.items()}
	epilogue_ints = {key: read_push_int(pc_to_instruction, pc) for key, pc in EPILOGUE_INT_PCS.items()}
	epilogue_ints["sequence_wait_frames"] = read_int_list(pc_to_instruction, EPILOGUE_SEQUENCE_WAIT_PCS)
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"embedded_event_scripts": read_string_list(string_entries, pc_to_string_index, COMMON_EVENT_SCRIPT_PCS),
		"money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PC)[0],
		"opening_sequence": opening,
		"staff_roll_text_ids": {key: read_int_list(pc_to_instruction, pcs) for key, pcs in STAFF_ROLL_TEXT_ID_PCS.items()},
		"check_and_prompt": {
			"required_flag": read_push_int(pc_to_instruction, CHECK_AND_PROMPT_PCS["required_flag"]),
			"cancelled_label": read_push_str(string_entries, pc_to_string_index, CHECK_AND_PROMPT_PCS["cancelled_label"])[0],
		},
		"epilogue_sequence": {
			"assets": epilogue,
			"timing_and_audio": epilogue_ints,
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_EVENT_SCRIPT_PCS, semantic["embedded_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["money_item_script"]])
	opening = semantic["opening_sequence"]
	write_push_int(pc_to_instruction, OPENING_PCS["bgm_domestic"], opening["bgm_domestic"])
	write_push_int(pc_to_instruction, OPENING_PCS["bgm_international"], opening["bgm_international"])
	for key in ["event_name", "model_brres", "motion_open", "motion_request", "widescreen_move", "widescreen_loop", "normal_move", "normal_loop", "placement_event", "placement_group", "placement_target"]:
		write_push_str_group(string_entries, pc_to_string_index, [OPENING_PCS[key]], [opening[key]])
	write_push_int(pc_to_instruction, OPENING_PCS["placement_species_min"], opening["placement_species_min"])
	write_push_int(pc_to_instruction, OPENING_PCS["placement_species_max"], opening["placement_species_max"])
	for key, pcs in STAFF_ROLL_TEXT_ID_PCS.items():
		write_int_list(pc_to_instruction, pcs, semantic["staff_roll_text_ids"][key], f"staff_roll_text_ids.{key}")
	write_push_int(pc_to_instruction, CHECK_AND_PROMPT_PCS["required_flag"], semantic["check_and_prompt"]["required_flag"])
	write_push_str_group(string_entries, pc_to_string_index, [CHECK_AND_PROMPT_PCS["cancelled_label"]], [semantic["check_and_prompt"]["cancelled_label"]])
	assets = semantic["epilogue_sequence"]["assets"]
	for key, pc in EPILOGUE_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [assets[key]])
	timing = semantic["epilogue_sequence"]["timing_and_audio"]
	for key, pc in EPILOGUE_INT_PCS.items():
		write_push_int(pc_to_instruction, pc, timing[key])
	write_int_list(pc_to_instruction, EPILOGUE_SEQUENCE_WAIT_PCS, timing["sequence_wait_frames"], "epilogue_sequence.timing_and_audio.sequence_wait_frames")


def command_export(args):
	command_export_impl(args, export_semantic)


def command_import(args):
	command_import_impl(args, apply_semantic)


def command_verify(args):
	command_verify_impl(args, export_semantic)


def build_arg_parser():
	parser = argparse.ArgumentParser(description="Edit CGameEnding.pkc via semantic JSON.")
	subparsers = parser.add_subparsers(dest="command", required=True)
	p = subparsers.add_parser("export")
	p.add_argument("pkc_path")
	p.add_argument("output", nargs="?")
	p.set_defaults(func=command_export)
	p = subparsers.add_parser("import")
	p.add_argument("json_path")
	p.add_argument("original_pkc")
	p.add_argument("output_pkc", nargs="?")
	p.set_defaults(func=command_import)
	p = subparsers.add_parser("verify")
	p.add_argument("pkc_path")
	p.set_defaults(func=command_verify)
	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
