package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	backend := os.Getenv("BACKEND")
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"backend": backend, "path": r.URL.RequestURI()})
	})
	server := &http.Server{Addr: ":8080", Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	log.Fatal(server.ListenAndServe())
}
