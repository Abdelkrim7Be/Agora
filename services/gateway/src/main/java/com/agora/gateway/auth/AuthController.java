package com.agora.gateway.auth;

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

    public AuthController(UserRepository users, PasswordEncoder passwordEncoder, JwtService jwtService) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
    }

    record LoginRequest(@NotBlank String username, @NotBlank String password) {}
    record TokenResponse(String token) {}

    @PostMapping("/auth/login")
    public ResponseEntity<?> login(@Valid @RequestBody LoginRequest req) {
        Optional<AppUser> user = users.findByUsername(req.username());
        if (user.isEmpty() || !passwordEncoder.matches(req.password(), user.get().getPasswordHash())) {
            return ResponseEntity.status(401).body(Map.of("error", "invalid credentials"));
        }
        String token = jwtService.generate(user.get().getUsername(), user.get().getRole());
        return ResponseEntity.ok(new TokenResponse(token));
    }
}
