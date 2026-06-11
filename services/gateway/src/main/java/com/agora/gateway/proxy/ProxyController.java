package com.agora.gateway.proxy;

import com.agora.gateway.config.GatewayProperties;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StreamUtils;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;

import java.io.IOException;
import java.net.URI;
import java.util.Set;

@RestController
public class ProxyController {

    private static final String PREFIX = "/api/agent";

    private static final Set<String> HOP_BY_HOP = Set.of(
            "host", "connection", "content-length", "transfer-encoding",
            "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"
    );

    private final RestClient restClient;
    private final String upstreamBase;

    public ProxyController(GatewayProperties props, RestClient.Builder builder) {
        this.upstreamBase = props.getUpstream().getEmailAgentUrl();
        this.restClient = builder.build();
    }

    @RequestMapping("/api/agent/**")
    public ResponseEntity<byte[]> proxy(HttpServletRequest request) throws IOException {
        String downstreamPath = request.getRequestURI();
        String upstreamPath = downstreamPath.startsWith(PREFIX)
                ? downstreamPath.substring(PREFIX.length())
                : downstreamPath;

        String query = request.getQueryString();
        String upstreamUrl = upstreamBase + upstreamPath + (query != null ? "?" + query : "");

        HttpMethod method = HttpMethod.valueOf(request.getMethod());
        byte[] body = StreamUtils.copyToByteArray(request.getInputStream());
        String contentType = request.getContentType();

        var spec = restClient.method(method).uri(URI.create(upstreamUrl));

        // Forward safe headers (skip hop-by-hop)
        var headerNames = request.getHeaderNames();
        while (headerNames.hasMoreElements()) {
            String name = headerNames.nextElement();
            if (!HOP_BY_HOP.contains(name.toLowerCase())) {
                spec = spec.header(name, request.getHeader(name));
            }
        }

        if (body.length > 0 && contentType != null) {
            spec = spec.contentType(MediaType.parseMediaType(contentType)).body(body);
        }

        return spec.exchange((req, resp) -> {
            byte[] responseBody = resp.getBody().readAllBytes();
            return ResponseEntity
                    .status(resp.getStatusCode())
                    .contentType(resp.getHeaders().getContentType() != null
                            ? resp.getHeaders().getContentType()
                            : MediaType.APPLICATION_OCTET_STREAM)
                    .body(responseBody);
        });
    }

    @RequestMapping("/health")
    public ResponseEntity<Object> health() {
        return ResponseEntity.ok(java.util.Map.of("status", "ok"));
    }
}
