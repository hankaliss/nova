import { Dialog } from "./Dialog";
import { Btn } from "./Btn";

export interface AuthChallenge {
  server: string;
  url: string;
  code: string;
}

/**
 * Shown when an MCP server answers a tool call with an authorization
 * challenge (e.g. github-mcp-server without a token). The user completes
 * the login in their browser, presses "signed in" to unblock the server,
 * then retries the request.
 */
export function McpAuthDialog({
  challenge,
  onDismiss,
  onSignedIn,
}: {
  challenge: AuthChallenge | null;
  onDismiss: () => void;
  onSignedIn: () => void;
}) {
  if (!challenge) return null;
  const openBrowser = async () => {
    // The main process opens the URL it captured itself (system browser);
    // window.open would be refused by the window-open handler anyway.
    await window.nova.openAuthUrl?.();
  };
  const signedIn = () => {
    void window.nova.call("mcpRetryAuth", { name: challenge.server });
    onSignedIn();
  };
  return (
    <Dialog
      title="MCP server login required"
      onClose={onDismiss}
      width={480}
      footer={
        <>
          <Btn variant="ghost" onClick={onDismiss}>
            Later
          </Btn>
          <Btn variant="ghost" onClick={() => void openBrowser()}>
            Reopen login page
          </Btn>
          <Btn variant="accent" onClick={signedIn}>
            I've signed in
          </Btn>
        </>
      }
    >
      <div className="mb-2 text-[12.5px] text-subtext">
        The <span className="font-bold text-accent">{challenge.server}</span>{" "}
        MCP server needs browser authorization before its tools can run. Your
        default browser should have opened the login page automatically.
      </div>
      {challenge.code && (
        <>
          <div className="mb-1 text-[12.5px] text-subtext">
            Confirm this code there (pre-filled when possible):
          </div>
          <button
            className="group mb-3 flex w-full cursor-pointer items-center justify-between rounded-md border border-border bg-bg px-3 py-2"
            onClick={() => void window.nova.copyText?.(challenge.code)}
            title="Copy code"
          >
            <span className="font-mono text-xl text-text">
              {challenge.code}
            </span>
            <span className="text-[12.5px] text-meta group-hover:text-text">
              copy
            </span>
          </button>
        </>
      )}
      <ol className="list-inside list-decimal space-y-1 text-[13px] leading-relaxed text-subtext">
        <li>authorize in your external browser (code above, if shown)</li>
        <li>press "I've signed in" here</li>
        <li>retry your request in Nova</li>
      </ol>
    </Dialog>
  );
}
