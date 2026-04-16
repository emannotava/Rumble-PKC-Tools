
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

FORMAT_VERSION = "cdummy-ball-semantic-json-1"
COMMON_EVENT_SCRIPT_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PCS = [0x075F, 0x1F67]
ENERGY_TIERS = [
	{"threshold_pc": 0x1651, "model_pc": 0x1655, "loop_pc": 0x1659},
	{"threshold_pc": 0x1660, "model_pc": 0x1664, "loop_pc": 0x1668},
	{"threshold_pc": 0x166D, "model_pc": 0x1671, "loop_pc": 0x1675},
	{"threshold_pc": None, "model_pc": 0x167A, "loop_pc": 0x167E},
]
WATCH_VISUAL_PCS = {
	"heal_model_brres": 0x1EF3,
	"watch_model_brres": 0x1EF8,
	"watch_motion": 0x1EFC,
}
REWARD_TABLE_START_PC = 0x181D
REWARD_TABLE_CALL_TARGET = 6280
REWARD_TABLE_FIELD_NAMES = ["group", "difficulty", "slot", "value_a", "value_b"]


def parse_reward_rows(pc_to_instruction):
	rows = []
	pc = REWARD_TABLE_START_PC
	while True:
		values = []
		cursor = pc
		valid = True
		for _ in range(5):
			instruction = pc_to_instruction.get(cursor)
			if instruction is None or instruction["opname"] != "push_int":
				valid = False
				break
			values.append(read_push_int(pc_to_instruction, cursor))
			cursor += 1
		if not valid:
			break
		call_instruction = pc_to_instruction.get(cursor)
		if call_instruction is None or call_instruction["opname"] != "call_imm":
			break
		call_target = int(call_instruction.get("decoded", {}).get("value"))
		if call_target != REWARD_TABLE_CALL_TARGET:
			break
		cursor += 1
		pop_instruction = pc_to_instruction.get(cursor)
		if pop_instruction is None or pop_instruction["opname"] != "pop":
			break
		row = {name: value for name, value in zip(REWARD_TABLE_FIELD_NAMES, values)}
		row["call_target"] = call_target
		rows.append(row)
		pc = cursor + 1
	return rows


def apply_reward_rows(pc_to_instruction, rows):
	original_rows = parse_reward_rows(pc_to_instruction)
	if len(rows) != len(original_rows):
		raise ValueError(f"reward_rows must contain {len(original_rows)} rows, got {len(rows)}")
	pc = REWARD_TABLE_START_PC
	for row in rows:
		for field_name in REWARD_TABLE_FIELD_NAMES:
			write_push_int(pc_to_instruction, pc, row[field_name])
			pc += 1
		call_instruction = pc_to_instruction.get(pc)
		if call_instruction is None or call_instruction["opname"] != "call_imm":
			raise ValueError(f"Expected call_imm at PC 0x{pc:04X}")
		pc += 2


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"embedded_event_scripts": read_string_list(string_entries, pc_to_string_index, COMMON_EVENT_SCRIPT_PCS),
		"money_item_scripts": read_string_list(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PCS),
		"energy_visuals": {
			"tiers": [
				{
					"threshold_lt": None if config["threshold_pc"] is None else read_push_int(pc_to_instruction, config["threshold_pc"]),
					"model_brres": read_push_str(string_entries, pc_to_string_index, config["model_pc"])[0],
					"loop_motion": read_push_str(string_entries, pc_to_string_index, config["loop_pc"])[0],
				}
				for config in ENERGY_TIERS
			],
		},
		"watch_visuals": {
			key: read_push_str(string_entries, pc_to_string_index, pc)[0]
			for key, pc in WATCH_VISUAL_PCS.items()
		},
		"reward_rows": parse_reward_rows(pc_to_instruction),
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_EVENT_SCRIPT_PCS, semantic["embedded_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PCS, semantic["money_item_scripts"])
	tiers = semantic["energy_visuals"]["tiers"]
	if len(tiers) != len(ENERGY_TIERS):
		raise ValueError(f"energy_visuals.tiers must contain {len(ENERGY_TIERS)} rows")
	for config, entry in zip(ENERGY_TIERS, tiers):
		if config["threshold_pc"] is not None:
			write_push_int(pc_to_instruction, config["threshold_pc"], entry["threshold_lt"])
		write_push_str_group(string_entries, pc_to_string_index, [config["model_pc"]], [entry["model_brres"]])
		write_push_str_group(string_entries, pc_to_string_index, [config["loop_pc"]], [entry["loop_motion"]])
	for key, pc in WATCH_VISUAL_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["watch_visuals"][key]])
	apply_reward_rows(pc_to_instruction, semantic["reward_rows"])


def command_export(args):
	command_export_impl(args, export_semantic)


def command_import(args):
	command_import_impl(args, apply_semantic)


def command_verify(args):
	command_verify_impl(args, export_semantic)


def build_arg_parser():
	parser = argparse.ArgumentParser(description="Edit CDummy_ball.pkc via semantic JSON.")
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
