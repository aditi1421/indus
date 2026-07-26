package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestMatchTrigger_NoPrefix(t *testing.T) {
	text, ok := matchTrigger("  hello there  ", "")
	if !ok {
		t.Fatalf("expected ok=true when no prefix configured")
	}
	if text != "hello there" {
		t.Fatalf("expected trimmed text, got %q", text)
	}
}

func TestMatchTrigger_EmptyMessage(t *testing.T) {
	if _, ok := matchTrigger("   ", ""); ok {
		t.Fatalf("expected ok=false for blank message")
	}
}

func TestMatchTrigger_PrefixMatchStripsAndTrims(t *testing.T) {
	text, ok := matchTrigger("@nyaya what can you do?", "@nyaya")
	if !ok {
		t.Fatalf("expected ok=true when prefix matches")
	}
	if text != "what can you do?" {
		t.Fatalf("expected stripped text, got %q", text)
	}
}

func TestMatchTrigger_PrefixCaseInsensitive(t *testing.T) {
	text, ok := matchTrigger("@NYAYA hello", "@nyaya")
	if !ok {
		t.Fatalf("expected ok=true for case-insensitive prefix match")
	}
	if text != "hello" {
		t.Fatalf("expected stripped text, got %q", text)
	}
}

func TestMatchTrigger_PrefixNoMatch(t *testing.T) {
	if _, ok := matchTrigger("hello there", "@nyaya"); ok {
		t.Fatalf("expected ok=false when message doesn't start with prefix")
	}
}

func TestMatchTrigger_PrefixWithoutBoundary(t *testing.T) {
	// "@nyaya" should NOT match "@Nyayafoo hi" (no word boundary)
	if _, ok := matchTrigger("@Nyayafoo hi", "@nyaya"); ok {
		t.Fatalf("expected ok=false when prefix has no word boundary")
	}
}

func TestMatchTrigger_PrefixAlone(t *testing.T) {
	// "@nyaya" alone (with no following text) should match and return empty string
	text, ok := matchTrigger("@nyaya", "@nyaya")
	if !ok {
		t.Fatalf("expected ok=true for prefix alone")
	}
	if text != "" {
		t.Fatalf("expected empty string after stripping prefix, got %q", text)
	}
}

func TestMatchTrigger_PrefixWithSpace(t *testing.T) {
	// "@nyaya " (with trailing space) should match and return empty string
	text, ok := matchTrigger("@nyaya ", "@nyaya")
	if !ok {
		t.Fatalf("expected ok=true for prefix with space")
	}
	if text != "" {
		t.Fatalf("expected empty string after stripping prefix and space, got %q", text)
	}
}

func TestSenderName_PrefersPushName(t *testing.T) {
	if got := senderName("Alice", "919999999999"); got != "Alice" {
		t.Fatalf("expected pushname, got %q", got)
	}
}

func TestSenderName_FallsBackToPhoneNumber(t *testing.T) {
	if got := senderName("", "919999999999"); got != "919999999999" {
		t.Fatalf("expected phone fallback, got %q", got)
	}
}

func TestSendHandler_BadBody_MissingText(t *testing.T) {
	called := false
	h := newSendHandler(func(text string) error {
		called = true
		return nil
	})
	req := httptest.NewRequest(http.MethodPost, "/send", bytes.NewReader([]byte(`{}`)))
	w := httptest.NewRecorder()
	h(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
	if called {
		t.Fatalf("expected send not to be called for bad body")
	}
}

func TestSendHandler_BadBody_InvalidJSON(t *testing.T) {
	h := newSendHandler(func(text string) error { return nil })
	req := httptest.NewRequest(http.MethodPost, "/send", bytes.NewReader([]byte(`not json`)))
	w := httptest.NewRecorder()
	h(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestSendHandler_GoodBody_InvokesSendAndReturnsOK(t *testing.T) {
	var gotText string
	h := newSendHandler(func(text string) error {
		gotText = text
		return nil
	})
	payload, _ := json.Marshal(map[string]string{"Text": "test push from clerk"})
	req := httptest.NewRequest(http.MethodPost, "/send", bytes.NewReader(payload))
	w := httptest.NewRecorder()
	h(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	if gotText != "test push from clerk" {
		t.Fatalf("expected send to be called with body text, got %q", gotText)
	}
	var out struct{ Ok bool }
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatalf("expected valid json response, got %q: %v", w.Body.String(), err)
	}
	if !out.Ok {
		t.Fatalf("expected ok:true in response, got %q", w.Body.String())
	}
}

func TestNormalizePhone(t *testing.T) {
	cases := []struct {
		name    string
		in      string
		want    string
		wantErr bool
	}{
		{"plus and spaces stripped", "+91 92174 80551", "919217480551", false},
		{"already normalized", "919217480551", "919217480551", false},
		{"garbage input errors", "abc", "", true},
		{"empty input errors", "", "", true},
		{"dashes and plus stripped", "+1-555-123-4567", "15551234567", false},
		{"letters mixed in errors", "9217a80551", "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := normalizePhone(tc.in)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("expected error for input %q, got none (result %q)", tc.in, got)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error for input %q: %v", tc.in, err)
			}
			if got != tc.want {
				t.Fatalf("normalizePhone(%q) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}

// review finding: non-200 /chat responses (and undecodable bodies) used to
// return "" silently, indistinguishable from a deliberate silent ignore
// (like a 403). They should now surface a user-facing error message, and
// log the status + a truncated body server-side.
func TestAskAgent_NonOKStatus_ReturnsInternalProblemMessage(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("boom: something broke upstream"))
	}))
	defer srv.Close()

	origURL := agentURL
	agentURL = srv.URL
	defer func() { agentURL = origURL }()

	got := askAgent("sender", "hello")
	if got != "The clerk service had an internal problem." {
		t.Fatalf("expected internal-problem message, got %q", got)
	}
}

func TestAskAgent_Forbidden_ReturnsEmptySilently(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer srv.Close()

	origURL := agentURL
	agentURL = srv.URL
	defer func() { agentURL = origURL }()

	got := askAgent("sender", "hello")
	if got != "" {
		t.Fatalf("expected silent empty string for 403, got %q", got)
	}
}

func TestAskAgent_BadJSON_ReturnsInternalProblemMessage(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("not json"))
	}))
	defer srv.Close()

	origURL := agentURL
	agentURL = srv.URL
	defer func() { agentURL = origURL }()

	got := askAgent("sender", "hello")
	if got != "The clerk service had an internal problem." {
		t.Fatalf("expected internal-problem message for bad JSON, got %q", got)
	}
}

func TestAskAgent_OK_ReturnsReply(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"Reply": "hi there"})
	}))
	defer srv.Close()

	origURL := agentURL
	agentURL = srv.URL
	defer func() { agentURL = origURL }()

	got := askAgent("sender", "hello")
	if got != "hi there" {
		t.Fatalf("expected reply from body, got %q", got)
	}
}

func TestSendHandler_SendErrorStillReturnsOK(t *testing.T) {
	// Behavior contract only guarantees a 400 on bad body; a downstream send
	// error is logged, not surfaced as an HTTP failure (fire-and-forget outbound).
	h := newSendHandler(func(text string) error {
		return errors.New("boom")
	})
	payload, _ := json.Marshal(map[string]string{"Text": "hi"})
	req := httptest.NewRequest(http.MethodPost, "/send", bytes.NewReader(payload))
	w := httptest.NewRecorder()
	h(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 even when send fails, got %d", w.Code)
	}
}
