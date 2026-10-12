// Kein @types/react-test-renderer im Workspace-Dependency-Graph von
// apps/mobile verlinkt und keine neue Abhaengigkeit erlaubt (Betriebshinweise
// dieses Auftrags: package.json/pnpm-lock.yaml bleiben unveraendert) - ein
// reiner `declare module "react-test-renderer";`-Platzhalter reicht aber
// nicht: Component-Tests im Projekt nutzen sowohl den benannten Typ-Import
// (`import { act, create, type ReactTestRenderer } from "react-test-renderer"`)
// als auch den Default-Import mit Namespace-Zugriff
// (`import TestRenderer, { act } from "react-test-renderer"; let tree:
// TestRenderer.ReactTestRenderer`) - beides braucht echte benannte Typen
// statt eines pauschalen `any`.
//
// Die Typen unten sind fachlich identisch zu @types/react-test-renderer
// (DefinitelyTyped, MIT-Lizenz), das ueber jest-expo transitiv im pnpm-Store
// liegt (siehe pnpm-lock.yaml), aber ausserhalb von apps/mobile/node_modules
// - deshalb hier als Ambient-Deklaration nachgebildet. Betrifft nur den
// Typecheck, nicht das Laufzeitverhalten: zur Laufzeit laedt Jest weiterhin
// das echte, zur React-Version passende Paket.
declare module "react-test-renderer" {
  import type { ElementType, ReactElement } from "react";

  export interface ReactTestRendererJSON {
    type: string;
    props: { [propName: string]: any };
    children: null | ReactTestRendererNode[];
  }

  export type ReactTestRendererNode = ReactTestRendererJSON | string;

  export interface ReactTestRendererTree extends ReactTestRendererJSON {
    nodeType: "component" | "host";
    instance: any;
    rendered: null | ReactTestRendererTree | ReactTestRendererTree[];
  }

  export interface ReactTestInstance {
    instance: any;
    type: ElementType;
    props: { [propName: string]: any };
    parent: null | ReactTestInstance;
    children: (ReactTestInstance | string)[];

    find(predicate: (node: ReactTestInstance) => boolean): ReactTestInstance;
    findByType(type: ElementType): ReactTestInstance;
    findByProps(props: { [propName: string]: any }): ReactTestInstance;

    findAll(
      predicate: (node: ReactTestInstance) => boolean,
      options?: { deep: boolean },
    ): ReactTestInstance[];
    findAllByType(type: ElementType, options?: { deep: boolean }): ReactTestInstance[];
    findAllByProps(
      props: { [propName: string]: any },
      options?: { deep: boolean },
    ): ReactTestInstance[];
  }

  export interface ReactTestRenderer {
    toJSON(): null | ReactTestRendererJSON | ReactTestRendererJSON[];
    toTree(): null | ReactTestRendererTree;
    unmount(nextElement?: ReactElement): void;
    update(nextElement: ReactElement): void;
    getInstance(): null | ReactTestInstance;
    root: ReactTestInstance;
  }

  export interface TestRendererOptions {
    createNodeMock(element: ReactElement): any;
  }

  export function create(
    nextElement: ReactElement,
    options?: TestRendererOptions,
  ): ReactTestRenderer;

  export function act(callback: () => Promise<void>): Promise<undefined>;
  export function act(callback: () => void): void;
}
