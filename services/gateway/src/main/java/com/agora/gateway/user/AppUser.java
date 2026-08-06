package com.agora.gateway.user;

import jakarta.persistence.CollectionTable;
import jakarta.persistence.Column;
import jakarta.persistence.ElementCollection;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.Table;

import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "app_user")
public class AppUser {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(unique = true, nullable = false)
    private String username;

    /** Where an invitation is delivered. Nullable: users created before invitations
     * existed have none, and the seeder still creates users without one. */
    private String email;

    @Column(nullable = false)
    private String passwordHash;

    @Column(nullable = false)
    private String role;

    /** How the person wants to be named in the interface. Held here rather than in
     * the browser: it used to live in localStorage, so it was lost on a new
     * machine and invisible to everyone else. */
    private String displayName;

    /** Free-text department label (HR, Finance, ...); references the same vocabulary
     * as the email-agent role/actor directory. Null for users with no department
     * scope (owner, admin, viewer). */
    private String department;

    @Column(nullable = false)
    private boolean enabled = true;

    /** Base32 TOTP shared secret. Set by /auth/mfa/setup, only takes effect (mfaEnabled)
     * once the owner proves possession via /auth/mfa/confirm. */
    private String mfaSecret;

    @Column(nullable = false)
    private boolean mfaEnabled = false;

    /** BCrypt-hashed single-use recovery codes, consumed (removed) on use. */
    @ElementCollection(fetch = FetchType.EAGER)
    @CollectionTable(name = "app_user_mfa_recovery_code", joinColumns = @JoinColumn(name = "app_user_id"))
    @Column(name = "code_hash", nullable = false)
    private List<String> mfaRecoveryCodeHashes = new ArrayList<>();

    public AppUser() {}

    public AppUser(String username, String passwordHash, String role) {
        this.username = username;
        this.passwordHash = passwordHash;
        this.role = role;
    }

    public AppUser(String username, String passwordHash, String role, String department) {
        this(username, passwordHash, role);
        this.department = department;
    }

    public Long getId() { return id; }
    public String getUsername() { return username; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
    public void setPasswordHash(String passwordHash) { this.passwordHash = passwordHash; }
    public String getPasswordHash() { return passwordHash; }
    public String getRole() { return role; }
    public void setRole(String role) { this.role = role; }
    public String getDepartment() { return department; }
    public void setDepartment(String department) { this.department = department; }

    public String getDisplayName() { return displayName; }
    public void setDisplayName(String displayName) { this.displayName = displayName; }
    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }

    public String getMfaSecret() { return mfaSecret; }
    public void setMfaSecret(String mfaSecret) { this.mfaSecret = mfaSecret; }

    public boolean isMfaEnabled() { return mfaEnabled; }
    public void setMfaEnabled(boolean mfaEnabled) { this.mfaEnabled = mfaEnabled; }

    public List<String> getMfaRecoveryCodeHashes() { return mfaRecoveryCodeHashes; }
    public void setMfaRecoveryCodeHashes(List<String> mfaRecoveryCodeHashes) { this.mfaRecoveryCodeHashes = mfaRecoveryCodeHashes; }
}
