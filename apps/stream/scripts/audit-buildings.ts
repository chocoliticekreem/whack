/**
 * Headless collision audit for the twin's buildings. Needs the dev server for the OSM geometry.
 *   npx jiti apps/stream/scripts/audit-buildings.ts [http://localhost:3001/api/twin/osm]
 */
import type { TwinGeometry } from "../src/twin/config";
import { buildNetwork } from "../src/twin/network/graph";
import { auditItems, auditRaw } from "../src/twin/scene/buildings/audit";
import { RoadIndex, clearRoads } from "../src/twin/scene/buildings/clearance";
import { resolveBuildings } from "../src/twin/scene/buildings/overlaps";

const url = process.argv[2] ?? "http://localhost:3001/api/twin/osm";
const geometry = (await (await fetch(url)).json()) as TwinGeometry;
const roads = new RoadIndex(buildNetwork(geometry));

const toItems = (placed: ReturnType<typeof resolveBuildings>) => placed.map((p) => ({ fp: p.fp, minHeight: p.minHeight, height: p.height }));
const rows = {
  "raw OSM": auditRaw(geometry.buildings, roads),
  "after dedupe": auditItems(toItems(resolveBuildings(geometry.buildings)), roads),
  "after dedupe + road clearance": auditItems(toItems(resolveBuildings(geometry.buildings, roads)), roads),
};
console.table(rows);
console.log("road clearance:", clearRoads(geometry.buildings, roads).stats);
