// types.d.ts

// ----- Unions & helpers (types) -----
export type RPCMessageType =
    | "ack"
    | "hello"
    | "welcome"
    | "request"
    | "emit"
    | "reply"
    | "subscribe"
    | "stateUpdate"
    | "unsubscribe"
    | "cancel"
    | "error";

export type Dict = Record<string, unknown>

export type Payload<T = unknown> = T;

export interface Gen {
    num: number;
    salt: string;
}

export interface RPCMessage<TPayload = unknown> {
    v: string;              // RPCMessage schema version
    id: string;             // UUIDv7
    correlatesTo?: string;  // UUIDv7 of previous message, if in sequence.
    type: RPCMessageType;
    lane: string;           // "sys" or other lane name; "noLaneSet" or "noValidRouteLane" if lane is not set
    gen: Gen;
    ts: number;             // Monotonic time of sending
    budgetMs?: number;      // How many ms to finish job and communication
    ackOf?: number;
    job?: Dict;
    idempotencyKey?: string;
    route?: Route;
    args?: unknown[];
    op?: string;
    seq?: number;           // Per-lane delivery sequence number
    path?: string;
    origin?: Dict;          // For metadata only, not for auth
    chunkNo?: number;       // For streamed payload
    final?: boolean;        // For streamed payload
    payload: Payload<TPayload>;
}

export interface ModManifest {
    id: string;
    name: string;
    version: string;
    entry: string;
    permissions: string[];
    capabilities: string[];
    enabled: boolean;
    hash: string;
}

export interface Route {
    capability: string;
    object?: string;
}

export interface Invocation {
    route: Route;
    path?: string;
    op?: string;
}

export type SubscribeCtx = {
    id: string;
    origin: Dict;
    signal: AbortSignal;
    push: (payload: unknown) => void;
}

export type SubscribeResult = {
    onCancel?: () => void;
    initial?: unknown;
};

export type EventMap = Record<string, unknown>;

export interface Subscription<E extends EventMap = Record<string, unknown>> {
    id: string;
    on: <K extends keyof E>(event: K, fn: (data: E[K]) => void) => void;
    _emit: <K extends keyof E>(event: K, data: E[K]) => void;
    close: () => void;
    invocation: Invocation;
    opts: Dict;
}
