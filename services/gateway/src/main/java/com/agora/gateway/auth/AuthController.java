package com.agora.gateway.auth;

import com.agora.gateway.audit.AuditService;
import com.agora.gateway.security.JwtService;
import com.agora.gateway.user.AppUser;
import com.agora.gateway.user.UserRepository;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.ResponseEntity;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.Optional;

@RestController
public class AuthController {

    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    private final AuditService auditService;

    public AuthController(UserRepository users, PasswordEncoder passwordEncoder,
                          JwtService jwtService, AuditService auditService) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
        this.auditService = auditService;
    }

    record LoginRequest(@NotBlank String username, @NotBlank String password) {}
    record TokenResponse(String token) {}

    @PostMapping("/auth/login")
    public ResponseEntity<?> login(@Valid @RequestBody LoginRequest req) {
        Optional<AppUser> user = users.findByUsername(req.username());
        boolean passwordOk = user.isPresent()
                && passwordEncoder.matches(req.password(), user.get().getPasswordHash());
        if (!passwordOk || !user.get().isEnabled()) {
            auditService.record(req.username(), null, "login", "POST", "/auth/login", null, "failure");
            return ResponseEntity.status(401).body(Map.of("error", "invalid credentials"));
        }
        AppUser u = user.get();
        auditService.record(u.getUsername(), u.getRole(), "login", "POST", "/auth/login", null, "success");
        String token = jwtService.generate(u.getUsername(), u.getRole());
        return ResponseEntity.ok(new TokenResponse(token));
    }
}
