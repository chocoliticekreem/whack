"use client";

import { Component, type ReactNode } from "react";

interface Props {
  name: string;
  children: ReactNode;
}

interface State {
  failed: boolean;
}

/** A crashing scene module disappears; the rest of the world keeps rendering. */
export class ModuleBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`[twin] module "${this.props.name}" crashed`, error);
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
