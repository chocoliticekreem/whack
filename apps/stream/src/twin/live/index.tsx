"use client";

import { CompareOverlay } from "../compare/CompareOverlay";
import { Agents } from "./Agents";
import { Buses } from "./Buses";
import { Cameras } from "./Cameras";
import { Disruptions } from "./Disruptions";
import { Hud } from "./Hud";
import { LabelHost, LabelLayer } from "./Label";
import { PredictLayers } from "../predict/Ghost";
import { PredictPanel } from "../predict/Panel";
import { ReplayHud } from "../predict/ReplayHud";
import { ReportModal } from "../predict/ReportModal";
import { FieldDebug } from "../sim/FieldDebug";
import { useField } from "../sim/useField";
import { useScenarioFeeds } from "./scenario";
import { useLiveFeeds } from "./store";
import { Streamer } from "./Streamer";
import { RouteLine } from "../route/RouteLine";

/**
 * Live data layers rendered inside the <Canvas>: real TfL cams, YOLO tracks, buses, disruptions. Also
 * hosts the field poll and scenario feeds that drive the Traffic and Pedestrians modules.
 */
export function LiveLayers() {
  useLiveFeeds();
  useField();
  useScenarioFeeds();
  return (
    <group>
      <FieldDebug />
      <Agents />
      <Cameras />
      <Buses />
      <Disruptions />
      <Streamer />
      <PredictLayers />
      <RouteLine />
      <LabelLayer />
    </group>
  );
}

/** DOM overlay rendered outside the <Canvas>. */
export function LiveHud() {
  return (
    <>
      <LabelHost />
      <Hud />
      <CompareOverlay />
      <PredictPanel />
      <ReportModal />
      <ReplayHud />
    </>
  );
}
