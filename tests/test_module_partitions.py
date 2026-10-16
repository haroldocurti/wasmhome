import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def partition_rows():
    content = (ROOT / "partitions.csv").read_text(encoding="utf-8")
    rows = []
    for row in csv.reader(line for line in content.splitlines()
                          if line.strip() and not line.lstrip().startswith("#")):
        name, kind, subtype, offset, size, *_ = (field.strip() for field in row)
        rows.append({"name": name, "kind": kind, "subtype": subtype,
                     "offset": int(offset, 0), "size": int(size, 0)})
    return rows


class ModulePartitionTests(unittest.TestCase):
    def test_module_slots_are_aligned_disjoint_and_follow_existing_ota_layout(self):
        rows = partition_rows()
        by_name = {row["name"]: row for row in rows}

        # Preserve the established boot, NVS, and OTA layout used by issue #14.
        expected = {
            "nvs": (0x9000, 0x6000),
            "otadata": (0xF000, 0x2000),
            "phy_init": (0x11000, 0x1000),
            "ota_0": (0x20000, 0x700000),
            "ota_1": (0x720000, 0x700000),
        }
        for name, (offset, size) in expected.items():
            self.assertEqual((by_name[name]["offset"], by_name[name]["size"]),
                             (offset, size))

        slots = {row["name"]: row for row in rows if row["name"].endswith("_slot_a")
                 or row["name"].endswith("_slot_b")}
        self.assertEqual(set(slots), {
            "interp_slot_a", "interp_slot_b", "aot_slot_a", "aot_slot_b"})
        self.assertTrue(all(row["kind"] == "data" and row["subtype"] == "0x40"
                            for row in slots.values()))
        self.assertTrue(all(row["size"] == 0x10000 and row["offset"] % 0x10000 == 0
                            for row in slots.values()))
        self.assertEqual(len(slots), 4)
        self.assertTrue(all(len(name) < 16 for name in slots))
        self.assertEqual(min(row["offset"] for row in slots.values()), 0xE20000)
        self.assertEqual(max(row["offset"] + row["size"] for row in slots.values()),
                         0xE60000)

        ranges = sorted((row["offset"], row["offset"] + row["size"], row["name"])
                        for row in rows)
        for (_, previous_end, previous_name), (offset, _, name) in zip(ranges, ranges[1:]):
            self.assertLessEqual(previous_end, offset,
                                 f"partition overlap: {previous_name} and {name}")
        self.assertLessEqual(ranges[-1][1], 16 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
