package io.ics.runtime.tools;

import io.ics.runtime.ICSRuntimeException;

/** Thrown when a tool call is blocked by a deny flag. */
public class ToolDeniedException extends ICSRuntimeException {

    private final String toolName;
    private final String reason;

    public ToolDeniedException(String toolName, String reason) {
        super("Tool '" + toolName + "' denied: " + reason);
        this.toolName = toolName;
        this.reason   = reason;
    }

    public String getToolName() { return toolName; }
    public String getReason()   { return reason; }
}
