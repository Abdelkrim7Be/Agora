package com.agora.gateway.auth;

import com.fasterxml.jackson.annotation.JsonProperty;

public record TokenResponse(
        String token,
        @JsonProperty("access_token") String accessToken,
        @JsonProperty("token_type") String tokenType,
        @JsonProperty("expires_in") long expiresIn,
        @JsonProperty("refresh_expires_in") long refreshExpiresIn
) {}
