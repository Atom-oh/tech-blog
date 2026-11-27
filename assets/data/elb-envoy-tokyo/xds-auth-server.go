// Minimal ADS server that authorizes each stream by the client certificate's URI SAN.
// server mode: mTLS gRPC on :18000; node.cluster must equal the last segment of
//
//	spiffe://example.test/elb-proxy/<name> on EVERY request that carries a node.
//
// client mode: open ADS through an Envoy HTTP listener (h2c) to see what a routed caller gets.
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	clusterv3 "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	core "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	listenerv3 "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	routev3 "github.com/envoyproxy/go-control-plane/envoy/config/route/v3"
	_ "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/http/router/v3"
	_ "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/network/http_connection_manager/v3"
	_ "github.com/envoyproxy/go-control-plane/envoy/extensions/transport_sockets/tls/v3"
	_ "github.com/envoyproxy/go-control-plane/envoy/extensions/upstreams/http/v3"
	discovery "github.com/envoyproxy/go-control-plane/envoy/service/discovery/v3"
	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	cachev3 "github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	resource "github.com/envoyproxy/go-control-plane/pkg/resource/v3"
	serverv3 "github.com/envoyproxy/go-control-plane/pkg/server/v3"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const sanPrefix = "spiffe://example.test/elb-proxy/"

func logj(m map[string]any) {
	m["t"] = time.Now().UTC().Format(time.RFC3339Nano)
	b, _ := json.Marshal(m)
	fmt.Println(string(b))
}

type byCluster struct{}

func (byCluster) ID(n *core.Node) string { return n.GetCluster() }

type peerInfo struct{ san, serial string }

func must[T proto.Message](js string, m T) T {
	if err := protojson.Unmarshal([]byte(js), m); err != nil {
		log.Fatalf("unmarshal %T: %v", m, err)
	}
	return m
}

func snapshot(name string, port int) *cachev3.Snapshot {
	lis := must(fmt.Sprintf(`{"name":"http","address":{"socket_address":{"address":"127.0.0.1","port_value":%d}},
 "filter_chains":[{"filters":[{"name":"envoy.filters.network.http_connection_manager","typed_config":{
  "@type":"type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
  "stat_prefix":"http","codec_type":"AUTO",
  "rds":{"route_config_name":"local","config_source":{"ads":{},"resource_api_version":"V3"}},
  "http_filters":[{"name":"envoy.filters.http.router","typed_config":{"@type":"type.googleapis.com/envoy.extensions.filters.http.router.v3.Router"}}]}}]}]}`, port), &listenerv3.Listener{})
	rt := must(fmt.Sprintf(`{"name":"local","virtual_hosts":[{"name":"all","domains":["*"],"routes":[
  {"match":{"prefix":"/envoy.service.discovery.v3.AggregatedDiscoveryService/"},"route":{"cluster":"xds_cluster","timeout":"0s"}},
  {"match":{"prefix":"/probe/new-cluster"},"route":{"cluster":"xds-probe-plain"}},
  {"match":{"prefix":"/"},"direct_response":{"status":200,"body":{"inline_string":"%s v1\n"}}}]}]}`, name), &routev3.RouteConfiguration{})
	cl := must(`{"name":"xds-probe-plain","type":"STATIC","connect_timeout":"2s",
  "load_assignment":{"cluster_name":"xds-probe-plain","endpoints":[{"lb_endpoints":[{"endpoint":{"address":{"socket_address":{"address":"127.0.0.1","port_value":18000}}}}]}]},
  "typed_extension_protocol_options":{"envoy.extensions.upstreams.http.v3.HttpProtocolOptions":{
    "@type":"type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions","explicit_http_config":{"http2_protocol_options":{}}}},
  "transport_socket":{"name":"envoy.transport_sockets.tls","typed_config":{
    "@type":"type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext","sni":"xds.example.test"}}}`, &clusterv3.Cluster{})
	s, err := cachev3.NewSnapshot("v1", map[resource.Type][]types.Resource{
		resource.ListenerType: {lis}, resource.RouteType: {rt}, resource.ClusterType: {cl},
	})
	if err != nil {
		log.Fatal(err)
	}
	return s
}

func runServer(dir string) {
	ctx := context.Background()
	cache := cachev3.NewSnapshotCache(true, byCluster{}, nil)
	for name, port := range map[string]int{"order-gw": 10080, "pay-gw": 10081} {
		if err := cache.SetSnapshot(ctx, name, snapshot(name, port)); err != nil {
			log.Fatal(err)
		}
	}
	var mu sync.Mutex
	peers := map[int64]peerInfo{}
	cb := serverv3.CallbackFuncs{
		// RegisterAggregatedDiscoveryServiceServer also exposes DeltaAggregatedResources, and
		// go-control-plane allows a Delta request when no Delta callback is set. The bootstrap uses
		// SotW (api_type: GRPC), so refuse Delta outright instead of leaving it unauthorized.
		DeltaStreamOpenFunc: func(ctx context.Context, id int64, typ string) error {
			logj(map[string]any{"ev": "delta_reject", "stream": id})
			return status.Error(codes.Unimplemented, "delta xDS disabled")
		},
		StreamClosedFunc: func(id int64, _ *core.Node) {
			mu.Lock()
			delete(peers, id)
			mu.Unlock()
		},
		StreamOpenFunc: func(ctx context.Context, id int64, typ string) error {
			pi := peerInfo{}
			if p, ok := peer.FromContext(ctx); ok {
				if ti, ok := p.AuthInfo.(credentials.TLSInfo); ok && len(ti.State.PeerCertificates) > 0 {
					leaf := ti.State.PeerCertificates[0]
					if len(leaf.URIs) > 0 {
						pi.san = leaf.URIs[0].String()
					}
					pi.serial = leaf.SerialNumber.Text(16)
				}
			}
			md, _ := metadata.FromIncomingContext(ctx)
			keys := []string{}
			for k := range md {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			mu.Lock()
			peers[id] = pi
			mu.Unlock()
			logj(map[string]any{"ev": "stream_open", "stream": id, "san": pi.san, "serial": pi.serial, "metadata_keys": keys})
			return nil
		},
		StreamRequestFunc: func(id int64, req *discovery.DiscoveryRequest) error {
			n := req.GetNode()
			if n == nil { // set_node_on_first_message_only: later messages omit the node
				return nil
			}
			mu.Lock()
			pi := peers[id]
			mu.Unlock()
			want := strings.TrimPrefix(pi.san, sanPrefix)
			ok := pi.san != "" && strings.HasPrefix(pi.san, sanPrefix) && n.GetCluster() == want
			logj(map[string]any{"ev": "authz", "stream": id, "san": pi.san, "node_id": n.GetId(), "node_cluster": n.GetCluster(), "type": req.GetTypeUrl(), "allow": ok})
			if !ok {
				return status.Errorf(codes.PermissionDenied, "node.cluster %q not allowed for %q", n.GetCluster(), pi.san)
			}
			return nil
		},
	}
	srv := serverv3.NewServer(ctx, cache, cb)

	cert, err := tls.LoadX509KeyPair(dir+"/xds-server.crt", dir+"/xds-server.key")
	if err != nil {
		log.Fatal(err)
	}
	caPEM, err := os.ReadFile(dir + "/proxy-ca.crt")
	if err != nil {
		log.Fatal(err)
	}
	pool := x509.NewCertPool()
	pool.AppendCertsFromPEM(caPEM)
	tcfg := &tls.Config{
		Certificates: []tls.Certificate{cert},
		ClientCAs:    pool,
		ClientAuth:   tls.VerifyClientCertIfGiven, // so a missing cert is logged below instead of silently failing
		MinVersion:   tls.VersionTLS12,
		NextProtos:   []string{"h2"},
		VerifyConnection: func(cs tls.ConnectionState) error {
			if len(cs.PeerCertificates) == 0 {
				logj(map[string]any{"ev": "tls_reject", "reason": "no client certificate"})
				return fmt.Errorf("client certificate required")
			}
			return nil
		},
	}
	gs := grpc.NewServer(grpc.Creds(credentials.NewTLS(tcfg)))
	discovery.RegisterAggregatedDiscoveryServiceServer(gs, srv)
	ln, err := net.Listen("tcp", "127.0.0.1:18000")
	if err != nil {
		log.Fatal(err)
	}
	logj(map[string]any{"ev": "listening", "addr": "127.0.0.1:18000"})
	log.Fatal(gs.Serve(ln))
}

// runClient opens ADS through an Envoy HTTP listener as an ordinary external caller would.
func runClient(target, cluster string, delta bool, certDir, certName string) {
	creds := insecure.NewCredentials()
	if certDir != "" { // direct mTLS to the xDS server with a proxy certificate
		c, err := tls.LoadX509KeyPair(certDir+"/"+certName+".crt", certDir+"/"+certName+".key")
		if err != nil {
			log.Fatal(err)
		}
		caPEM, err := os.ReadFile(certDir + "/xds-server-ca.crt")
		if err != nil {
			log.Fatal(err)
		}
		pool := x509.NewCertPool()
		pool.AppendCertsFromPEM(caPEM)
		creds = credentials.NewTLS(&tls.Config{Certificates: []tls.Certificate{c}, RootCAs: pool, ServerName: "xds.example.test"})
	}
	conn, err := grpc.NewClient(target, grpc.WithTransportCredentials(creds))
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	out := map[string]any{"ev": "client", "target": target, "node_cluster": cluster, "delta": delta, "direct_mtls": certDir != ""}
	if delta {
		ds, err := discovery.NewAggregatedDiscoveryServiceClient(conn).DeltaAggregatedResources(ctx)
		if err == nil {
			err = ds.Send(&discovery.DeltaDiscoveryRequest{Node: &core.Node{Id: "external-caller", Cluster: cluster}, TypeUrl: resource.ListenerType})
		}
		if err == nil {
			var resp *discovery.DeltaDiscoveryResponse
			resp, err = ds.Recv()
			if err == nil {
				names := []string{}
				for _, r := range resp.GetResources() {
					var l listenerv3.Listener
					if r.GetResource().UnmarshalTo(&l) == nil {
						names = append(names, fmt.Sprintf("%s@%d", l.GetName(), l.GetAddress().GetSocketAddress().GetPortValue()))
					}
				}
				out["received_listeners"] = names
			}
		}
		if err != nil {
			s, _ := status.FromError(err)
			out["grpc_code"] = s.Code().String()
			out["error"] = s.Message()
		}
		logj(out)
		return
	}
	st, err := discovery.NewAggregatedDiscoveryServiceClient(conn).StreamAggregatedResources(ctx)
	if err == nil {
		err = st.Send(&discovery.DiscoveryRequest{Node: &core.Node{Id: "external-caller", Cluster: cluster}, TypeUrl: resource.ListenerType})
	}
	if err == nil {
		var resp *discovery.DiscoveryResponse
		resp, err = st.Recv()
		if err == nil {
			names := []string{}
			for _, r := range resp.GetResources() {
				var l listenerv3.Listener
				if r.UnmarshalTo(&l) == nil {
					names = append(names, fmt.Sprintf("%s@%d", l.GetName(), l.GetAddress().GetSocketAddress().GetPortValue()))
				}
			}
			out["received_listeners"] = names
			out["version"] = resp.GetVersionInfo()
		}
	}
	if err != nil {
		s, _ := status.FromError(err)
		out["grpc_code"] = s.Code().String()
		out["error"] = s.Message()
	}
	logj(out)
}

func main() {
	mode := flag.String("mode", "server", "server|client")
	dir := flag.String("certs", "", "server cert dir")
	target := flag.String("target", "127.0.0.1:10080", "client: Envoy listener")
	cluster := flag.String("cluster", "order-gw", "client: node.cluster to claim")
	delta := flag.Bool("delta", false, "client: use DeltaAggregatedResources")
	certName := flag.String("cert", "", "client: proxy cert name in -certs dir for direct mTLS")
	flag.Parse()
	if *mode == "client" {
		d := ""
		if *certName != "" {
			d = *dir
		}
		runClient(*target, *cluster, *delta, d, *certName)
		return
	}
	runServer(*dir)
}
