"""
Zone classifier.

Loads zone bounding-box definitions from store_layout.json and classifies
(x, y) points — typically the midpoint of a detection bounding box — into
named zones via axis-aligned polygon (rectangle) containment.

If a point falls outside all defined zones, zone_id is None.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pipeline.zones")


@dataclass(frozen=True)
class Zone:
    zone_id: str
    sku_zone: Optional[str]
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 (normalised 0-1)
    is_entry: bool = False
    is_billing: bool = False


class ZoneClassifier:
    """
    Maps a normalised (x, y) foot-point to the corresponding zone.

    store_layout.json format expected:
    {
      "stores": {
        "STORE_BLR_001": {
          "cameras": {
            "CAM_BLR_001_FLOOR": {
              "zones": {
                "SKINCARE": {
                  "bbox": [x1, y1, x2, y2],
                  "sku_zone": "SKINCARE",
                  "is_entry": false,
                  "is_billing": false
                }
              }
            }
          }
        }
      }
    }
    """

    def __init__(self, layout_path: str, store_id: str, camera_id: str):
        self.store_id = store_id
        self.camera_id = camera_id
        self._zones: list[Zone] = []
        self._load(layout_path)

    def _load(self, layout_path: str) -> None:
        data = json.loads(Path(layout_path).read_text())
        store = data.get("stores", {}).get(self.store_id, {})
        # Zones are at store level; filter to those belonging to this camera
        raw_zones = {
            zone_id: zdef
            for zone_id, zdef in store.get("zones", {}).items()
            if zdef.get("camera_id") == self.camera_id
        }

        for zone_id, zdef in raw_zones.items():
            bbox = zdef.get("bbox", [])
            if len(bbox) != 4:
                logger.warning("Zone %s has invalid bbox, skipping", zone_id)
                continue
            self._zones.append(
                Zone(
                    zone_id=zone_id,
                    sku_zone=zdef.get("sku_zone"),
                    bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                    is_entry=bool(zdef.get("is_entry", False)),
                    is_billing=bool(zdef.get("is_billing", False)),
                )
            )
        logger.info(
            "Loaded %d zones for %s/%s", len(self._zones), self.store_id, self.camera_id
        )

    def classify(
        self, nx: float, ny: float
    ) -> Optional[Zone]:
        """
        Classify a normalised coordinate (nx, ny) into a zone.

        Returns the first matching Zone, or None if outside all zones.
        Priority: entry zone > billing zone > others (order of definition).
        """
        entry_match: Optional[Zone] = None
        billing_match: Optional[Zone] = None
        general_match: Optional[Zone] = None

        for zone in self._zones:
            x1, y1, x2, y2 = zone.bbox
            if x1 <= nx <= x2 and y1 <= ny <= y2:
                if zone.is_entry:
                    entry_match = zone
                elif zone.is_billing:
                    billing_match = zone
                else:
                    general_match = general_match or zone

        return entry_match or billing_match or general_match

    @property
    def all_zones(self) -> list[Zone]:
        return list(self._zones)
