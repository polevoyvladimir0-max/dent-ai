ui = true
disable_mlock = true

storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr = "http://localhost:8200"
cluster_addr = "http://vault:8201"

# Auto-unseal via Transit (external "seal" Vault)
seal "transit" {
  address         = "{{ env "VAULT_SEAL_ADDR" }}"
  token           = "{{ env "VAULT_SEAL_TOKEN" }}"
  key_name        = "{{ env "VAULT_SEAL_KEY_NAME" }}"
  mount_path      = "{{ env "VAULT_SEAL_MOUNT_PATH" }}"
  tls_skip_verify = {{ env "VAULT_SEAL_TLS_SKIP_VERIFY" }}
}
