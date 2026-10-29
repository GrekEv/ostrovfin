package intelligencebff

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

func (c *Client) Search(ctx context.Context, body []byte) ([]byte, int, error) {
	if len(body) == 0 || len(body) > 16384 {
		return nil, http.StatusBadRequest, errors.New("invalid request size")
	}
	base, err := url.Parse(c.BaseURL)
	if err != nil || (base.Scheme != "http" && base.Scheme != "https") || base.Host == "" {
		return nil, http.StatusInternalServerError, errors.New("invalid intelligence endpoint")
	}
	base.Path = strings.TrimSuffix(base.Path, "/") + "/api/v2/intelligence/search"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, base.String(), bytes.NewReader(body))
	if err != nil {
		return nil, http.StatusInternalServerError, err
	}
	req.Header.Set("Content-Type", "application/json")
	httpClient := c.HTTP
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 12 * time.Second}
	}
	res, err := httpClient.Do(req)
	if err != nil {
		return nil, http.StatusBadGateway, err
	}
	defer res.Body.Close()
	data, err := io.ReadAll(io.LimitReader(res.Body, 1<<20))
	if err != nil {
		return nil, http.StatusBadGateway, err
	}
	return data, res.StatusCode, nil
}
