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
