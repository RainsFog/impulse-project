"""
test_generate.py — pytest-тесты для generate.py

Запуск из папки impulse_project/:
    pytest tests/test_generate.py -v
"""

import json
import pytest
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from generate import (
    parse_model,
    parse_multiplicity,
    build_children_map,
    generate_config_xml,
    generate_meta_json,
    generate_delta,
    apply_delta,
    _topological_order,
)

BASE_DIR  = Path(__file__).parent.parent
INPUT_DIR = BASE_DIR / "input"


# ФИКСТУРЫ

@pytest.fixture(scope="session")
def model():
    """Парсим UML-модель один раз на всю сессию тестов."""
    return parse_model(str(INPUT_DIR / "impulse_test_input.xml"))

@pytest.fixture(scope="session")
def classes(model):
    return model[0]

@pytest.fixture(scope="session")
def aggregations(model):
    return model[1]

@pytest.fixture(scope="session")
def children_map(aggregations):
    return build_children_map(aggregations)

@pytest.fixture(scope="session")
def config_data():
    with open(INPUT_DIR / "config.json", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture(scope="session")
def patched_data():
    with open(INPUT_DIR / "patched_config.json", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture(scope="session")
def delta_data(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("delta")
    return generate_delta(
        str(INPUT_DIR / "config.json"),
        str(INPUT_DIR / "patched_config.json"),
        str(tmp / "delta.json"),
    )


# UNIT: parse_multiplicity

class TestParseMultiplicity:

    def test_single_value(self):
        assert parse_multiplicity("1") == ("1", "1")

    def test_range(self):
        assert parse_multiplicity("0..100") == ("0", "100")

    def test_range_zero_one(self):
        assert parse_multiplicity("0..1") == ("0", "1")

    def test_range_zero_many(self):
        assert parse_multiplicity("0..42") == ("0", "42")

    @pytest.mark.parametrize("value, expected_min, expected_max", [
        ("1",      "1", "1"),
        ("0..100", "0", "100"),
        ("0..1",   "0", "1"),
        ("0..42",  "0", "42"),
        ("5",      "5", "5"),
    ])
    def test_parametrized(self, value, expected_min, expected_max):
        min_val, max_val = parse_multiplicity(value)
        assert min_val == expected_min
        assert max_val == expected_max


# UNIT: Парс

class TestParseModel:

    def test_all_classes_loaded(self, classes):
        assert set(classes.keys()) == {"BTS", "MGMT", "COMM", "MetricJob", "CPLANE", "RU", "HWE"}

    def test_bts_is_root(self, classes):
        assert classes["BTS"]["isRoot"] is True

    def test_non_root_classes(self, classes):
        for name in ["MGMT", "COMM", "MetricJob", "CPLANE", "RU", "HWE"]:
            assert classes[name]["isRoot"] is False

    def test_bts_attributes(self, classes):
        names = [a["name"] for a in classes["BTS"]["attributes"]]
        assert "id" in names and "name" in names

    def test_ru_has_four_attributes(self, classes):
        assert len(classes["RU"]["attributes"]) == 4

    def test_mgmt_has_no_attributes(self, classes):
        assert classes["MGMT"]["attributes"] == []

    def test_aggregation_count(self, aggregations):
        assert len(aggregations) == 6

    def test_aggregation_pairs(self, aggregations):
        pairs = {(a["source"], a["target"]) for a in aggregations}
        assert ("MGMT", "BTS") in pairs
        assert ("RU", "HWE") in pairs
        assert ("MetricJob", "MGMT") in pairs

    def test_documentation_not_empty(self, classes):
        assert classes["BTS"]["documentation"] != ""
        assert classes["RU"]["documentation"] != ""


# UNIT: build_children_map

class TestBuildChildrenMap:

    def test_bts_children(self, children_map):
        assert set(children_map["BTS"]) == {"MGMT", "HWE", "COMM"}

    def test_mgmt_children(self, children_map):
        assert set(children_map["MGMT"]) == {"MetricJob", "CPLANE"}

    def test_hwe_children(self, children_map):
        assert children_map["HWE"] == ["RU"]

    def test_leaf_classes_not_in_map(self, children_map):
        for leaf in ["RU", "MetricJob", "CPLANE", "COMM"]:
            assert leaf not in children_map


# UNIT: delta

class TestDelta:

    def test_additions_exist(self, delta_data):
        assert len(delta_data["additions"]) > 0

    def test_additions_are_new_keys(self, delta_data, config_data, patched_data):
        for item in delta_data["additions"]:
            assert item["key"] not in config_data
            assert item["key"] in patched_data

    def test_deletions_exist(self, delta_data):
        assert len(delta_data["deletions"]) > 0

    def test_deletions_are_removed_keys(self, delta_data, config_data, patched_data):
        for key in delta_data["deletions"]:
            assert key in config_data
            assert key not in patched_data

    def test_updates_have_changed_values(self, delta_data, config_data, patched_data):
        for item in delta_data["updates"]:
            assert item["from"] == config_data[item["key"]]
            assert item["to"] == patched_data[item["key"]]
            assert item["from"] != item["to"]

    def test_additions_keys_start_with_added(self, delta_data):
        for item in delta_data["additions"]:
            assert item["key"].startswith("added_")

    def test_delta_has_all_three_sections(self, delta_data):
        assert "additions" in delta_data
        assert "deletions" in delta_data
        assert "updates" in delta_data


# INTEGRATION: apply_delta

class TestApplyDelta:

    def test_result_keys_match_patched(self, tmp_path, delta_data, patched_data):
        out = tmp_path / "res.json"
        apply_delta(str(INPUT_DIR / "config.json"), delta_data, str(out))
        result = json.loads(out.read_text())
        assert set(result.keys()) == set(patched_data.keys())

    def test_result_values_match_patched(self, tmp_path, delta_data, patched_data):
        out = tmp_path / "res.json"
        apply_delta(str(INPUT_DIR / "config.json"), delta_data, str(out))
        result = json.loads(out.read_text())
        for key in patched_data:
            assert result[key] == patched_data[key]

    def test_deleted_keys_absent_in_result(self, tmp_path, delta_data):
        out = tmp_path / "res.json"
        apply_delta(str(INPUT_DIR / "config.json"), delta_data, str(out))
        result = json.loads(out.read_text())
        for key in delta_data["deletions"]:
            assert key not in result


# INTEGRATION: config.xml

class TestConfigXml:

    @pytest.fixture(scope="class")
    def xml_root(self, tmp_path_factory, classes, aggregations):
        tmp = tmp_path_factory.mktemp("xml")
        out = tmp / "config.xml"
        generate_config_xml(classes, aggregations, str(out))
        return ET.parse(str(out)).getroot()

    def test_root_tag_is_bts(self, xml_root):
        assert xml_root.tag == "BTS"

    def test_bts_has_id_and_name(self, xml_root):
        tags = [child.tag for child in xml_root]
        assert "id" in tags and "name" in tags

    def test_id_type_is_uint32(self, xml_root):
        assert xml_root.find("id").text == "uint32"

    def test_mgmt_present_in_bts(self, xml_root):
        assert xml_root.find("MGMT") is not None

    def test_hwe_present_in_bts(self, xml_root):
        assert xml_root.find("HWE") is not None

    def test_ru_nested_in_hwe(self, xml_root):
        hwe = xml_root.find("HWE")
        assert hwe.find("RU") is not None

    def test_metricjob_nested_in_mgmt(self, xml_root):
        mgmt = xml_root.find("MGMT")
        assert mgmt.find("MetricJob") is not None

    def test_ru_attributes(self, xml_root):
        ru = xml_root.find(".//RU")
        assert {child.tag for child in ru} == {"hwRevision", "id", "ipv4Address", "manufacturerName"}


# INTEGRATION: meta.json

class TestMetaJson:

    @pytest.fixture(scope="class")
    def meta(self, tmp_path_factory, classes, aggregations):
        tmp = tmp_path_factory.mktemp("meta")
        out = tmp / "meta.json"
        generate_meta_json(classes, aggregations, str(out))
        return json.loads(out.read_text())

    def test_all_classes_present(self, meta):
        assert {e["class"] for e in meta} == {"BTS", "MGMT", "COMM", "MetricJob", "CPLANE", "RU", "HWE"}

    def test_bts_is_last(self, meta):
        assert meta[-1]["class"] == "BTS"

    def test_bts_no_min_max(self, meta):
        bts = next(e for e in meta if e["class"] == "BTS")
        assert "min" not in bts and "max" not in bts

    def test_metricjob_multiplicity(self, meta):
        mj = next(e for e in meta if e["class"] == "MetricJob")
        assert mj["min"] == "0" and mj["max"] == "100"

    def test_ru_multiplicity(self, meta):
        ru = next(e for e in meta if e["class"] == "RU")
        assert ru["min"] == "0" and ru["max"] == "42"

    def test_bts_parameters_include_child_classes(self, meta):
        bts = next(e for e in meta if e["class"] == "BTS")
        names = [p["name"] for p in bts["parameters"]]
        assert "MGMT" in names and "HWE" in names and "COMM" in names

    def test_child_class_params_have_type_class(self, meta):
        bts = next(e for e in meta if e["class"] == "BTS")
        assert len([p for p in bts["parameters"] if p["type"] == "class"]) == 3

    def test_ru_parameters_are_attributes(self, meta):
        ru = next(e for e in meta if e["class"] == "RU")
        names = [p["name"] for p in ru["parameters"]]
        assert "hwRevision" in names and "ipv4Address" in names

    @pytest.mark.parametrize("class_name", ["MetricJob", "CPLANE", "MGMT", "RU", "HWE", "COMM"])
    def test_non_root_classes_have_min_max(self, meta, class_name):
        entry = next(e for e in meta if e["class"] == class_name)
        assert "min" in entry and "max" in entry
