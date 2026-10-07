# Public Knowledge Consumer Gateway

This small gateway is a network boundary for the existing Mac Public Knowledge
service. It does not create a second RAG, index, data store, provider, or
Knowledge Production pipeline. Its upstream defaults to the existing service at
`http://127.0.0.1:9000`.

For same-machine use, run `start_mac_gateway.command`; it binds to loopback by
default. For Windows clients on the company LAN, bind it only to the Mac's
private LAN address, for example:

```sh
KNOWLEDGE_GATEWAY_BIND_HOST=192.168.1.20 KNOWLEDGE_GATEWAY_PORT=9001 \
  ./knowledge_consumer_gateway/start_mac_gateway.command
```

The Mac firewall should permit that port only on the company LAN. Storage users
then enter the single URL `http://192.168.1.20:9001`. Do not change the existing
Public Knowledge container port binding to `0.0.0.0`.

The gateway exposes only health, capability discovery, source/citation reads,
source snapshot reads, search, and ask. It denies Admin, Provider, import,
delete, and all other routes. Provider credentials remain exclusively in the
existing Public Knowledge service configuration.
