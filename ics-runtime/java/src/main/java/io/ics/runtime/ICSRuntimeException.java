package io.ics.runtime;

/** Base exception for all ICS Runtime errors. */
public class ICSRuntimeException extends RuntimeException {
    public ICSRuntimeException(String message)            { super(message); }
    public ICSRuntimeException(String message, Throwable cause) { super(message, cause); }
}
