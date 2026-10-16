export interface HostedApi {
  ready: () => boolean;
  session: () => Promise<unknown>;
  submit: (search: unknown) => Promise<unknown>;
  get: (id: string) => Promise<unknown>;
  cancel: (id: string) => Promise<unknown>;
  signOut: () => Promise<void>;
}
export function mountFlightFinder(hosted: HostedApi): () => void;

