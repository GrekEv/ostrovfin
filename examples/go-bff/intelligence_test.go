package intelligencebff

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSearch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v2/intelligence/search" || r.Method != http.MethodPost {
			t.Fatal("bad path or method")
		}
		body, _ := io.ReadAll(r.Body)
		if !strings.Contains(string(body), "кошка") {
			t.Fatal("query missing")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"results":[]}`)
	}))
	defer srv.Close()
	client := Client{BaseURL: srv.URL}
	body, code, err := client.Search(context.Background(), []byte(`{"query":"кошка"}`))
	if err != nil || code != 200 || string(body) != `{"results":[]}` {
		t.Fatalf("invalid result: %s %d %v", body, code, err)
	}
}
