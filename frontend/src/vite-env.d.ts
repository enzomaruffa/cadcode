/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_WS_URL?: string;
  readonly VITE_HTTP_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// three-cad-viewer ships no types; declare the surface we use (plan §2 glue).
declare module "three-cad-viewer" {
  export class Display {
    constructor(container: HTMLElement, options: Record<string, unknown>);
  }
  export class Viewer {
    constructor(
      display: Display,
      options: Record<string, unknown>,
      notifyCallback?: (change: Record<string, unknown>) => void,
    );
    render(shapes: unknown, renderOptions: Record<string, unknown>, viewerOptions: Record<string, unknown>): void;
    clear(): void;
    update?: (updateMarker: boolean, notify?: boolean) => void;
    setState?: (id: string, state: number[]) => void;
    dispose?: () => void;
  }
  export class Timer {
    constructor(name: string, timeit?: boolean);
  }
}

declare module "three-cad-viewer/dist/three-cad-viewer.css";
