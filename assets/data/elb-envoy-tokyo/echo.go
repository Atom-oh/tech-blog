package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

func main() {
	backend := os.Getenv("BACKEND")
	instance, err := os.Hostname()
	if err != nil {
		log.Fatal(err)
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		result := map[string]string{"backend": backend, "path": r.URL.RequestURI()}
		if strings.HasPrefix(r.URL.Path, "/capture/") {
			result["instance"] = instance
			result["hop"] = strings.Join(r.Header.Values("x-order-hop"), ",")
			result["source"] = r.Header.Get("x-order-source")
		}
		json.NewEncoder(w).Encode(result)
	})
	server := &http.Server{Addr: ":8080", Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	log.Fatal(server.ListenAndServe())
}
