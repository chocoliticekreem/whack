"use client";

import { useMemo } from "react";
import * as THREE from "three";
import { bearingToYaw, LANDMARKS, landmarkPos } from "../../landmarks";
import { Kit, KitMeshes } from "./kit";

const HALF = 131;
const WIDTH = 34;
const DECK_TOP = 8.4;
const PIERS = [-78.6, -26.2, 26.2, 78.6];

function buildLondonBridge(): Kit {
  const kit = new Kit();
  kit.box("concrete", WIDTH, 2.6, HALF * 2, 0, DECK_TOP - 2.6, 0);
  kit.box("deck", WIDTH - 3, 0.12, HALF * 2, 0, DECK_TOP, 0);
  for (const z of PIERS) kit.box("concrete", WIDTH - 4, 11.5, 5.5, 0, -5, z);
  for (const sx of [-1, 1]) {
    kit.box("concrete", 0.6, 1.2, HALF * 2, sx * (WIDTH / 2 - 0.3), DECK_TOP, 0);
    kit.box("lamp", 0.25, 0.25, HALF * 2, sx * (WIDTH / 2 - 0.3), DECK_TOP + 1.25, 0);
    for (let z = -HALF + 8; z <= HALF - 8; z += 13) {
      kit.box("steel", 0.2, 6.5, 0.2, sx * (WIDTH / 2 - 1.2), DECK_TOP, z);
      kit.add("lamp", new THREE.SphereGeometry(0.5, 6, 4), sx * (WIDTH / 2 - 1.2), DECK_TOP + 6.8, z);
    }
  }
  return kit;
}

export function LondonBridge() {
  const kit = useMemo(() => buildLondonBridge(), []);
  const [x, z] = landmarkPos(LANDMARKS.londonBridge);
  return (
    <group position={[x, 0, z]} rotation={[0, bearingToYaw(LANDMARKS.londonBridge.headingDeg), 0]}>
      <KitMeshes kit={kit} />
    </group>
  );
}
