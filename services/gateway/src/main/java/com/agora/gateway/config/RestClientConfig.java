package com.agora.gateway.config;

import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManager;
import org.apache.hc.core5.util.Timeout;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

@Configuration
public class RestClientConfig {

    @Bean
    public RestClient.Builder restClientBuilder() {
        // The default HttpComponentsClientHttpRequestFactory() builds an HttpClient5
        // pool capped at 5 connections per route with a 180s connection-request
        // timeout — every proxied call to the single upstream (email-agent) shares
        // that route, so a handful of concurrent polling requests (setup, gmail
        // status, drafts, inbox, notifications...) exhaust it and everything past
        // the 5th queues silently for up to 3 minutes before failing.
        PoolingHttpClientConnectionManager connectionManager = new PoolingHttpClientConnectionManager();
        connectionManager.setMaxTotal(200);
        connectionManager.setDefaultMaxPerRoute(100);

        RequestConfig requestConfig = RequestConfig.custom()
                .setConnectionRequestTimeout(Timeout.ofSeconds(10))
                .setResponseTimeout(Timeout.ofSeconds(150))
                // HttpClient5 follows redirects by default. The Gmail OAuth callback
                // now replies with a 303 pointing the browser back into the web app —
                // if this client followed it instead, it would fetch that URL itself
                // (from inside the gateway container, where it doesn't even resolve
                // the same way) and the browser would never see the redirect at all.
                // ProxyController must forward 3xx responses as-is.
                .setRedirectsEnabled(false)
                .build();

        CloseableHttpClient httpClient = HttpClients.custom()
                .setConnectionManager(connectionManager)
                .setDefaultRequestConfig(requestConfig)
                .build();

        return RestClient.builder()
                .requestFactory(new HttpComponentsClientHttpRequestFactory(httpClient));
    }
}
