# ADR-008 Configuration and Secret Lifecycle
STATUS=ACCEPTED

Decision:
Configuration precedence is environment/secret references -> approved local config -> packaged non-secret defaults.
Secrets are never committed, bundled, logged or stored in Hardware DB.
Runtime consumes provider credentials; Hardware stores only references/configuration.
Precheck exposes missing-secret/config codes without values.
Credential rotation is environment/config driven and requires no product-code change.
