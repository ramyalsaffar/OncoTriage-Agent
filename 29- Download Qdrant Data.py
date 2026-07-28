"""
Download ALL data from Qdrant Cloud before account deletion.

Run from Spyder (after running 01, 02, 03):
    exec(open(code_path + "download_qdrant.py").read())
"""


# =====================================================================
# Uses qdrant_client and results_path from exec chain (01 + 02 + 03)
# =====================================================================

import json
import time
from pathlib import Path

output_dir = data_path + "06- Qdrant Downloaded Data for Latest Full Run/"
Path(output_dir).mkdir(parents=True, exist_ok=True)


# =====================================================================
# LIST ALL COLLECTIONS
# =====================================================================

collections = qdrant_client.get_collections().collections
print(f"Found {len(collections)} collections:")
for c in collections:
    print(f"  - {c.name}")

try:
    all_aliases = qdrant_client.get_aliases()
    print(f"\nAliases: {all_aliases}")
except Exception:
    pass

print()


# =====================================================================
# DOWNLOAD EACH COLLECTION
# =====================================================================

for collection_info in collections:
    name = collection_info.name

    info = qdrant_client.get_collection(collection_name=name)
    point_count = info.points_count

    print(f"{'='*60}")
    print(f"Collection: {name}")
    print(f"  Points: {point_count}")

    if point_count == 0:
        print(f"  Empty. Skipping.")
        continue

    print(f"  Downloading...")

    all_points = []
    offset = None

    while True:
        scroll_result = qdrant_client.scroll(
            collection_name=name,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )

        points, next_offset = scroll_result

        for point in points:
            point_data = {
                "id": point.id,
                "payload": point.payload,
            }

            if isinstance(point.vector, dict):
                serialized_vectors = {}
                for vec_name, vec_val in point.vector.items():
                    if hasattr(vec_val, 'indices'):
                        serialized_vectors[vec_name] = {
                            "type": "sparse",
                            "indices": list(vec_val.indices),
                            "values": list(vec_val.values),
                        }
                    else:
                        serialized_vectors[vec_name] = {
                            "type": "dense",
                            "values": list(vec_val) if not isinstance(vec_val, list) else vec_val,
                        }
                point_data["vectors"] = serialized_vectors
            elif point.vector is not None:
                point_data["vectors"] = {
                    "default": {
                        "type": "dense",
                        "values": list(point.vector),
                    }
                }

            all_points.append(point_data)

        if next_offset is None:
            break
        offset = next_offset

        print(f"    {len(all_points)}/{point_count}...", end="\r")
        time.sleep(0.05)

    print(f"    {len(all_points)}/{point_count} done.    ")

    output_file = Path(output_dir) / f"{name}.json"

    with open(output_file, "w") as f:
        json.dump(
            {
                "collection_name": name,
                "point_count": len(all_points),
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "points": all_points,
            },
            f,
            indent=2,
        )

    file_size_mb = output_file.stat().st_size / (1024 * 1024)
    print(f"  Saved: {output_file.name} ({file_size_mb:.1f} MB)")
    print()


# =====================================================================
# SUMMARY
# =====================================================================

print("=" * 60)
print("DOWNLOAD COMPLETE")
print("=" * 60)

backup_files = list(Path(output_dir).glob("*.json"))
total_size = sum(f.stat().st_size for f in backup_files) / (1024 * 1024)

print(f"  Directory: {output_dir}")
print(f"  Collections: {len(backup_files)}")
print(f"  Total size: {total_size:.1f} MB")

for f in sorted(backup_files):
    size = f.stat().st_size / (1024 * 1024)
    print(f"    {f.name}: {size:.1f} MB")

print(f"\nQdrant data is safe.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Apr 12 13:17:38 2026

@author: ramyalsaffar
"""

