"""
generate.py — генератор артефактов для системы управления базовой станцией.

Входные файлы (папка input/):
  - impulse_test_input.xml  — UML-модель (классы + агрегации)
  - config.json             — исходная конфигурация
  - patched_config.json     — изменённая конфигурация

Выходные файлы (папка output/):
  - config.xml              — XML-дерево классов из UML-модели
  - meta.json               — мета-информация о классах
  - delta.json              — разница между config и patched_config
  - res_patched_config.json — результат применения delta к config
"""

import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path


# 1. ПАРСИНГ UML-МОДЕЛИ

def parse_model(xml_path: str) -> tuple[dict, list]:
    """
    Читает impulse_test_input.xml и возвращает:
      - classes: dict { имя_класса: { isRoot, documentation, attributes: [{name, type}] } }
      - aggregations: list [ { source, target, sourceMultiplicity, targetMultiplicity } ]

    Агрегация трактуется так:
      source — дочерний класс (включённый)
      target — родительский класс (включающий)
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    classes = {}
    for cls in root.findall("Class"):
        name = cls.attrib["name"]
        attrs = []
        for attr in cls.findall("Attribute"):
            attrs.append({"name": attr.attrib["name"], "type": attr.attrib["type"]})
        classes[name] = {
            "isRoot": cls.attrib.get("isRoot", "false").lower() == "true",
            "documentation": cls.attrib.get("documentation", ""),
            "attributes": attrs,
        }

    aggregations = []
    for agg in root.findall("Aggregation"):
        aggregations.append({
            "source": agg.attrib["source"],
            "target": agg.attrib["target"],
            "sourceMultiplicity": agg.attrib.get("sourceMultiplicity", "1"),
            "targetMultiplicity": agg.attrib.get("targetMultiplicity", "1"),
        })

    return classes, aggregations


def parse_multiplicity(multiplicity: str) -> tuple[str, str]:
    """
    Разбирает строку мощности агрегации:
      "1"      → ("1", "1")
      "0..100" → ("0", "100")
      "0..1"   → ("0", "1")
    """
    if ".." in multiplicity:
        parts = multiplicity.split("..")
        return parts[0], parts[1]
    return multiplicity, multiplicity


def build_children_map(aggregations: list) -> dict:
    """
    Строит словарь: родитель → [список дочерних классов].
    Порядок сохраняется как в XML.
    """
    children = {}
    for agg in aggregations:
        parent = agg["target"]
        child = agg["source"]
        if parent not in children:
            children[parent] = []
        children[parent].append(child)
    return children


# 2. создание config.xml

def build_xml_element(class_name: str, classes: dict, children_map: dict) -> ET.Element:
    """
    Рекурсивно строит XML-элемент для класса:
      - сначала добавляет атрибуты класса как дочерние теги (значение = тип)
      - потом рекурсивно добавляет дочерние классы
    """
    element = ET.Element(class_name)
    cls_info = classes[class_name]
    for attr in cls_info["attributes"]:
        child_tag = ET.SubElement(element, attr["name"])
        child_tag.text = attr["type"]
    for child_class in children_map.get(class_name, []):
        child_element = build_xml_element(child_class, classes, children_map)
        element.append(child_element)
    return element


def generate_config_xml(classes: dict, aggregations: list, output_path: str):
    """
    Генерирует config.xml:
    Находит root-класс (isRoot=true), строит дерево от него,
    красиво форматирует с отступами и записывает в файл.
    """
    root_class = next(name for name, info in classes.items() if info["isRoot"])
    children_map = build_children_map(aggregations)
    xml_root = build_xml_element(root_class, classes, children_map)
    raw_xml = ET.tostring(xml_root, encoding="unicode")
    pretty_xml = minidom.parseString(raw_xml).toprettyxml(indent="    ")
    lines = pretty_xml.split("\n")
    result = "\n".join(lines[1:])
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"✓ Сгенерирован {output_path}")


# 3. создание meta.json

def _topological_order(root: str, children_map: dict) -> list:
    """
    Возвращает классы в порядке: сначала самые глубокие листья,
    потом их родители, root — последним (DFS постфиксный обход).
    """
    visited = []
    seen = set()

    def dfs(node):
        if node in seen:
            return
        seen.add(node)
        for child in children_map.get(node, []):
            dfs(child)
        visited.append(node)

    dfs(root)
    return visited


def generate_meta_json(classes: dict, aggregations: list, output_path: str):
    """
    Генерирует meta.json — список объектов для каждого класса.

    Структура каждого объекта:
      - class: имя класса
      - documentation: описание
      - isRoot: bool
      - min, max: из sourceMultiplicity агрегации (только для не-root классов)
      - parameters: список атрибутов + дочерних классов (type="class")
    """
    source_multiplicity = {}
    for agg in aggregations:
        min_val, max_val = parse_multiplicity(agg["sourceMultiplicity"])
        source_multiplicity[agg["source"]] = {"min": min_val, "max": max_val}

    children_map = build_children_map(aggregations)
    root_class = next(name for name, info in classes.items() if info["isRoot"])
    ordered = _topological_order(root_class, children_map)

    result = []
    for class_name in ordered:
        cls_info = classes[class_name]
        entry = {
            "class": class_name,
            "documentation": cls_info["documentation"],
            "isRoot": cls_info["isRoot"],
        }
        if not cls_info["isRoot"] and class_name in source_multiplicity:
            entry["max"] = source_multiplicity[class_name]["max"]
            entry["min"] = source_multiplicity[class_name]["min"]
        params = []
        for attr in cls_info["attributes"]:
            params.append({"name": attr["name"], "type": attr["type"]})
        for child in children_map.get(class_name, []):
            params.append({"name": child, "type": "class"})
        entry["parameters"] = params
        result.append(entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4, ensure_ascii=False)
    print(f"✓ Сгенерирован {output_path}")


# 4. создание delta.json

def generate_delta(config_path: str, patched_path: str, output_path: str) -> dict:
    """
    Сравнивает config.json и patched_config.json, генерирует delta.json:
      additions — ключи есть в patched, нет в config
      deletions — ключи есть в config, нет в patched
      updates   — ключи есть в обоих, но значение изменилось
    """
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    with open(patched_path, encoding="utf-8") as f:
        patched = json.load(f)

    config_keys = set(config.keys())
    patched_keys = set(patched.keys())

    additions = [
        {"key": k, "value": patched[k]}
        for k in sorted(patched_keys - config_keys)
    ]
    deletions = sorted(config_keys - patched_keys)
    updates = [
        {"key": k, "from": config[k], "to": patched[k]}
        for k in sorted(config_keys & patched_keys)
        if config[k] != patched[k]
    ]

    delta = {"additions": additions, "deletions": deletions, "updates": updates}

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(delta, f, indent=4, ensure_ascii=False)
    print(f"✓ Сгенерирован {output_path}")
    return delta


# 5. создание res_patched_config.json

def apply_delta(config_path: str, delta: dict, output_path: str):
    """
    Применяет delta к config.json и записывает результат:
      1. Берём копию config
      2. Удаляем ключи из deletions
      3. Обновляем значения из updates
      4. Добавляем новые ключи из additions
    """
    with open(config_path, encoding="utf-8") as f:
        result = json.load(f)

    for key in delta["deletions"]:
        result.pop(key, None)
    for item in delta["updates"]:
        result[item["key"]] = item["to"]
    for item in delta["additions"]:
        result[item["key"]] = item["value"]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4, ensure_ascii=False)
    print(f"✓ Сгенерирован {output_path}")


# 6. ТОЧКА ВХОДА

def main():
    BASE_DIR = Path(__file__).parent
    input_dir = BASE_DIR / "input"
    output_dir = BASE_DIR / "output"
    output_dir.mkdir(exist_ok=True)

    xml_path     = input_dir / "impulse_test_input.xml"
    config_path  = input_dir / "config.json"
    patched_path = input_dir / "patched_config.json"

    print("Парсим UML-модель...")
    classes, aggregations = parse_model(str(xml_path))

    print("Генерируем артефакты...")
    generate_config_xml(classes, aggregations, str(output_dir / "config.xml"))
    generate_meta_json(classes, aggregations, str(output_dir / "meta.json"))

    delta = generate_delta(
        str(config_path),
        str(patched_path),
        str(output_dir / "delta.json")
    )
    apply_delta(str(config_path), delta, str(output_dir / "res_patched_config.json"))

    print("\nВсе артефакты успешно сгенерированы!")


if __name__ == "__main__":
    main()
