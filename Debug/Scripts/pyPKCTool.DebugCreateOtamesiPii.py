#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys
import struct

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = "debugcreateotamesipii-semantic-json-1"


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


def export_semantic(document):
	instructions = document["code_section"]["instructions"]
	prologue = instructions[0]
	payload_hex = prologue.get("payload_hex", "00000000")
	payload_u32 = int(payload_hex, 16) if payload_hex else 0
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"script_kind": "empty_stub",
		"instruction_count": len(instructions),
		"prologue_payload_u32": payload_u32,
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	instructions = document["code_section"]["instructions"]
	if len(instructions) != 2:
		raise ValueError("DebugCreateOtamesiPii.pkc no longer matches the expected 2-instruction stub shape")
	payload_u32 = int(semantic.get("prologue_payload_u32", 0))
	if not 0 <= payload_u32 <= 0xFFFFFFFF:
		raise ValueError("prologue_payload_u32 must fit in an unsigned 32-bit integer")
	instructions[0]["payload_hex"] = struct.pack(">I", payload_u32).hex().upper()
	instructions[0]["decoded"] = {
		"type": "function_prologue_payload",
		"value_u32": payload_u32,
		"value_hex": f"0x{payload_u32:08X}",
	}
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
		raise SystemExit(1)
	print("Roundtrip OK")


def build_arg_parser():
	parser = argparse.ArgumentParser(description="Edit DebugCreateOtamesiPii.pkc as a tiny stub script.")
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
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
