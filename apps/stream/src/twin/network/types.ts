/**
 * Contract for the road + pavement network. `buildNetwork(geometry)` (network/graph.ts) produces it;
 * roads, pavements, traffic and pedestrians all read it. Local metres, y up, +x east, +z south.
 * Traffic drives on the LEFT (UK): a car offsets to the left of its direction of travel.
 */
export type XZ = [number, number];

export interface RoadNode {
  id: number;
  x: number;
  z: number;
  /** Ids of edges that start or end at this node. */
  edges: number[];
  /** True when 3+ edges meet (a junction), false for a plain bend or dead end. */
  junction: boolean;
}

export interface RoadEdge {
  id: number;
  from: number;
  to: number;
  kind: "major" | "minor";
  /** Full width of the carriageway in metres. */
  width: number;
  oneway: boolean;
  bridge: boolean;
  /** Polyline from `from` to `to`, including both end points. */
  pts: XZ[];
  /** Cumulative distance along `pts` at each point; last entry is the edge length. */
  cum: number[];
  length: number;
}

/** A pedestrian walkway polyline (one side of a road, or a pedestrian path). */
export interface Footway {
  id: number;
  pts: XZ[];
  cum: number[];
  length: number;
  /** Ids of footways that touch this one's end points, for walking on. */
  neighbours: number[];
}

export interface RoadNetwork {
  nodes: RoadNode[];
  edges: RoadEdge[];
  footways: Footway[];
  /**
   * Point on an edge at `dist` metres from its `from` node, shifted `lateral` metres to the left of
   * the direction of travel when travelling from→to (negative = right). Writes [x, z] into `out`
   * and returns the heading in radians (atan2(dx, dz), 0 = +z/south, π/2 = +x/east).
   */
  sample(edgeId: number, dist: number, lateral: number, out: XZ): number;
  /** Same for a footway. */
  sampleFootway(footwayId: number, dist: number, out: XZ): number;
}
