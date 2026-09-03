package com.agora.gateway;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Malformed request bodies (unparsable JSON, wrong field types) throw before any
 * controller method runs, so they were falling through to the security layer's
 * generic entry point and answering 401 — indistinguishable from a bad-credentials
 * response. This gives a bad request shape its own, correct 400.
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<String> handleMalformedBody() {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body("{\"error\":\"malformed_request_body\"}");
    }
}
