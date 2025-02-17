# 2024-12-09 Implementation Plan: Proxy Frontend API Requests Through Nuxt

**Author**: @obulat

## Reviewers

- [ ] @dhruvkb
- [x] @krysal

## Project Links

- [Project Thread](https://github.com/WordPress/openverse/issues/3473)
- [Project Milestone](https://github.com/WordPress/openverse/milestone/35)

This project does not have a project proposal because the scope and rationale of
the project are clear, as defined in the project thread.

## Overview

Currently, client requests[^1] from the frontend to the API are sent without
authentication. As a result, strict API rate limiting on unauthenticated
requests would inadvertently throttle legitimate traffic from openverse.org
users. By proxying all client requests[^1] through the Nuxt server, we can
authenticate them and thus distinguish them from the anonymous traffic in the
API.

This implementation plan outlines how we will route all frontend traffic to the
API through Nuxt server routes — effectively “authenticating” these requests at
the API level — and configure Cloudflare rate-limiting to protect these routes
from excessive automated traffic that is against Openverse Terms of Service.

While this plan does not include lowering unauthenticated rate limits in the
API, it establishes the groundwork for it. This approach will ensure better
stability and fairness of the API service, while also preserving the normal user
experience on the frontend.

## Expected Outcomes

1. **Distinguishable frontend requests in the Django API**: For the Django API,
   the frontend requests are authenticated and no longer constrained by
   unauthenticated rate limits.

2. **The frontend is protected with Cloudflare rate-limiting rules**: The
   frontend pages are protected from automated abuse and suspicious traffic by
   Cloudflare rate-limiting, ensuring that legitimate users (including those in
   shared IP environments, such as schools and libraries) maintain reliable
   access without undue friction.

## Request Flow

Under this new approach, when a user visits https://openverse.org or, for
example, https://openverse.org/search/?q=cat, the browser request goes through
Cloudflare, which applies its rate-limiting based on IP (and session cookie or
the session header, once set). If the request is under the limit, Cloudflare
forwards it to the Nuxt (Nitro) server for server-side rendering (SSR). During
this SSR process, Nuxt sets a session cookie in the response if the user does
not already have one.

After that first SSR load, the user’s browser automatically includes the newly
issued session cookie and the session header on all subsequent requests. When
the user navigates to another page or makes another search, the client requests
either static assets (e.g., `/\_nuxt` routes for static resources such as
JavaScript or CSS files) or API data from `/api` routes. All these client-side
requests again go through Cloudflare first for rate-limiting checks. When
allowed, Cloudflare passes them on to Nuxt, which verifies the session cookie
and proxies any `/api` requests to the Django API.

By combining the session cookie and IP at the Cloudflare layer, Openverse can
apply rate-limiting on a per-user basis—even behind NAT or shared IP addresses.
Any request that lacks a valid cookie or exceeds rate limits can be challenged
or blocked by Cloudflare, preventing unauthorized or abusive usage. This setup
ensures that no request (initial or otherwise) bypasses Cloudflare’s checks, and
that all API traffic flows through Nuxt’s server rather than directly to Django.

```{mermaid}
sequenceDiagram
    participant U as User
    participant C as Cloudflare
    participant O as Nuxt Server
    participant NS as Nitro Server
    participant A as API
    U ->> C: request
    Note left of C: "Check the rate limits based on session cookie and IP"
    alt has-cookie-not-rate-limited
        C ->> O: request
        O ->> NS: request
        NS ->> A: Request authenticated with frontend credentials
        A ->> NS: response
        NS ->> O: response
        O ->> U: response
    else has-cookie-rate-limited
        C ->> U: challenge
    else has-no-cookie-not-rate-limited
        C ->> O: request
        O ->> NS: request with added cookie
    end

    U ->> C: client-side request, with the HTTP-only cookie
    C ->> NS: request
    NS ->> A: Request authenticated with frontend credentials
```

## Cloudflare Rate Limiting

Originally, I thought of using Cloudflare managed challenges to protect the Nuxt
server routes from abuse. However, this would have required issuing a challenge
to every new user, which would have been a poor user experience. Instead, we
will use Cloudflare rate limiting to protect the frontend app from abuse.

The rate limiting will be based on the IP and cookie combination if cookie is
available, or on IP (for requests without cookies), and will trigger a managed
challenge only when the request volume from a user exceeds the defined
threshold. This way, legitimate users will not be inconvenienced by challenges,
while automated traffic will be rate-limited and challenged.

The proxying rules should exclude the `/_nuxt/` routes and requests for static
files (such as ending with `.jpg`, `.css`, or `.csv`), since those requests are
indirectly rate-limited by the rate-limits on the main requests, and would be
difficult to set rate-limiting rules for.

- **Supported Browsers for Challenges**:
  [Cloudflare Documentation](https://developers.cloudflare.com/waf/reference/cloudflare-challenges/#supported-browsers)
  notes that managed challenges support modern browsers, but does not show the
  minimum supported versions. Users with older browsers may struggle,
  potentially creating accessibility issues.

- **Referer Header Changes After Challenge**: After passing a managed challenge,
  the referer header may change:
  [Referer Header after Challenge](https://developers.cloudflare.com/waf/reference/cloudflare-challenges/#referer-header)
  We must ensure this does not disrupt our request logic.

- **Multi-language Support**: Challenges support multiple languages [^2],
  although the number of supported languages is lower than the number of
  languages supported by Openverse.

### Impact on the Nuxt Server

- **Load on Nuxt CPU/memory** Proxying the requests by the Nuxt server will
  increase the server load. We need to ensure that the server can handle that
  load by monitoring and adjusting the values if necessary. The server proxy
  routes should be written in such a way that they are not consuming CPU
  resources heavily.

## Step-by-step plan

1. [Add the frontend feature flag](#add-the-frontend-feature-flag)
2. [Set up Cloudflare rate limiting for staging](#set-up-cloudflare-rate-limiting-for-staging)
3. [Add session to the init plugin](#add-session-to-the-init-plugin)
4. [Add the server plugin to create the Openverse API client and add it to the event context](#add-the-server-plugin-to-initialize-the-openverse-api-client-and-add-it-to-the-event-context)
5. [Set up Nuxt server proxy routes](#set-up-nuxt-server-proxy-routes)
6. [Use Nuxt server routes instead of the `api-client` in the frontend when the feature flag is on](#use-server-routes-in-the-frontend-when-the-feature-flag-is-on)
7. [Set up Cloudflare rate limiting for production](#set-up-cloudflare-rate-limiting-for-production)
8. [Switch on the flag in production](#switch-the-feature-flag-on-in-production)
9. [Monitor the Cloudflare dashboard and Sentry logs for challenge occurrences](#monitor-the-cloudflare-dashboard-and-sentry-logs-for-challenge-occurrences)

## Step Details

### Add the frontend feature flag

Add `proxy_requests` feature flag to `frontend/feat/feature-flags.json`:

```json
{
  "proxy_requests": {
    "description": "Proxy frontend requests through Nuxt server",
    "status": {
      "staging": "switchable",
      "production": "disabled"
    },
    "defaultState": "off",
    "storage": "cookie"
  }
}
```

### Set up Cloudflare rate limiting for staging

Add the initial rate limiting to the Nuxt server routes (`/api/**`) in the
infrastructure repository (_maintainers only_).

Cloudflare rules consist of two parts: the **expression** to match the request
and the **action** to take when the request matches the expression [^3].

#### Expression

There should be two rate limiting expressions:

- for the returning users - the combination of IP and the session cookie
- for new users - IP and no cookie. This will cause the new users from a shared
  IP see the managed challenge more often, if the IP is sending many requests.
  However, after the first request such a user will be subject to the first rate
  limiting counts (IP and session cookie).

```
(http.host eq "staging.openverse.org"
and (
    (http.request.headers["x-h3-session"][0] ne "")
    or
    (http.request.headers["Cookie"] contains "ovSession")
)
and not starts_with(http.request.uri.path, "/_nuxt/")
and not ends_with(http.request.uri.path, ".js")
and not ends_with(http.request.uri.path, ".css")
and not ends_with(http.request.uri.path, ".png")
and not ends_with(http.request.uri.path, ".jpg")
and not ends_with(http.request.uri.path, ".svg")
and not ends_with(http.request.uri.path, ".webp")

```

#### Action

The action should be `managed_challenge`, which will issue a challenge to the
user when the rate limit is exceeded.

#### Testing the rate limits

##### Shared IP scenario

To test that the rate limits work as expected in the shared IP scenario, we can
simulate two users accessing the service from the same IP address:

1. Use two different browsers (or one browser with an incognito window) to
   represent two users.
2. User 1: In one browser, simulate a user with a valid session cookie,
   confirming they are treated as authenticated and bypass stricter rate limits.
3. User 2: In the other browser, simulate an anonymous user without a session
   cookie. Verify that their requests are subject to stricter IP-based rate
   limits.
4. Verification:

- Both users function independently, even though they share the same IP.
- The authenticated user is not affected by the stricter limits applied to the
  anonymous user.
- It is easy for the anonymous user to pass the challenge and use the service
  after that.

The rate limits should be set very low (such as 4 requests per minute) for this
test to allow for quick testing.

##### Real rate limits

Calculate the actual rate limits based on the expected traffic. The rate limits
should be generous enough to accommodate normal user behavior. Later, they can
be made stricter to deter automated abuse.

### Add session to the `init` plugin

When the user sends the first request to any Openverse frontend URLs, the Nuxt
server should initialize their session. This will be done in the entry point for
all SSR requests, the `init.server.ts` plugin (currently
`init-stores.server.ts`). This will make all subsequent requests from the user
identifiable as single-user requests, even in shared IP environments.

- Rename the `frontend/src/plugins/init-stores.server.ts` plugin to
  `init.server.ts` to reflect that this plugin will be initializing the state on
  the server side, not only in the stores.
- Add a session using the `unjs/h3`
  [`useSession`](https://h3.unjs.io/examples/handle-session) hook to the
  `init.server.ts` plugin. This hook will add an encoded cookie and a header to
  requests. The session should:
  - use the `HMAC_SIGNING_SECRET` env variable to sign the session.
  - the session cookie should be `HttpOnly`, `Secure`, and `SameSite=Strict` to
    prevent client-side tampering and XSS attacks.

### Add the server plugin to initialize the Openverse API client and add it to the event context

Create a nitro plugin using
[`defineNitroPlugin`](https://nuxt.com/docs/guide/directory-structure/server#server-plugins)
in `server/plugins/proxy.ts`. This plugin should:

- initialize the
  [`OpenverseApiClient`](https://github.com/WordPress/openverse/tree/347878acf7620548a0628d6833885a479c30b9c1/packages/js/api-client).
  If the API secrets are set, use authenticated client. Otherwise, in
  development environment, use unauthenticated client, and in
  staging/production, throw an error, as the API requests from the frontend
  should always be authenticated. To access the secrets, the plugin should use
  the `process.env.[VARIABLE_NAME]`, not `runtimeConfig.VARIABLE_NAME` since the
  `runtimeConfig` is not available in the plugin body.
- add a middleware using `defineEventHandler` that adds the `OpenverseApiClient`
  as to the event context as `event.context.api_client`.

### Set up Nuxt Server proxy routes

- Create the Nuxt server routes under `/api/`:

  - `/api/search/[type]` - for search requests
  - `/api/[type]/[id]` - for single result requests
  - `/api/[type]/related/[id]` - for related media requests

- Create a `proxyApiRequest` function that accepts the H3 `event` object and
  forwards the request to the API.
  - `validateRequest` function should:
    - test that the session is valid using the `unjs/h3` `getSession` function,
      or throw a 401 `Unauthorized` error.
    - ensure that the request contains the required details (media type, and
      media id, if required for the specific route).
  - `generateApiUrl` function should generate the API request URL based on the
    request details (for example, `/api/search/image/?q=cat` should be converted
    to `<apiUrl>/v1/images/?q=cat`).
  - send the request to the API using the Openverse API client from
    `event.context.client`.
  - handle errors, extract data necessary for the search time Plausible event,
    and return the appropriate response to the client.

### Use server routes in the frontend when the feature flag is on

Create two fetch functions in the stores: one that uses the current direct API
requests with `api-service`, and the other that sends the request to the Nuxt
server route. The fetch function to use should be selected based on the feature
flag status.

The change will need to be added to the `related`, `single-media` and `media`
stores. Here's sample implementation in the media store,
`frontend/stores/media/index.ts`:

```ts
if (featureFlagStore.isOn("proxy_requests")) {
  const result = getProxiedResponse(mediaType, queryParams)
} else {
  const client = useApiClient()
  const result = await getApiClientResponse(client, mediaType, queryParams)
  if (result.error) {
    // handle the error
    return null
  }
  // handle the response
}

const getProxiedResponse = async (
  mediaType: string,
  queryParams: Record<string, string>
) => {
  try {
    const { eventPayload, data } = await $fetch(
      `/api/search/${mediaType}/?${queryParams}`
    )
    return { eventPayload, data }
  } catch (error) {
    // return the $fetch error
  }
}

const getApiClientResponse = async (
  client: ApiClient,
  mediaType: string,
  queryParams: Record<string, string>
) => {
  try {
    const { eventPayload, data } = await client.search(mediaType, queryParams)
    return { eventPayload, data }
  } catch (error) {
    // return the error
  }
}
```

The new functions should not call any composables that require the Nuxt context
(e.g., `useNuxtApp`) because `[nuxt]` is not available in nested functions.

### Set up Cloudflare rate limiting for production

The rate limiting rule should be similar to that of the staging environment.

The rates should be generous enough to accommodate normal user behavior. Later,
they can be made stricter to deter automated abuse.

[More information on how request counts are calculated](https://developers.cloudflare.com/waf/rate-limiting-rules/request-rate/)

### Switch the feature flag on in production

Set the `proxy_requests` feature flag to `enabled` in the production
environment. This will start routing all frontend requests through the Nuxt
server.

### Monitor the Cloudflare dashboard and Sentry logs for challenge occurrences

The following data need to be monitored:

- Cloudflare dashboard (Security > WAF > Rate limiting rules) shows the number
  of issued challenges, and the
  [Challenge solve rate (CSR)](https://developers.cloudflare.com/bots/concepts/challenge-solve-rate/)
  for the rule. The CSR should be very high since we expect that most of the
  frontend usage is not automated.
- Nuxt CPU and memory usage. In case the server is under heavy load, we might
  need to adjust the rate limits and/or frontend task values.

## Rejected Alternatives

- **Always Issuing a Challenge Without Thresholds**: Issuing a managed challenge
  to every new user, regardless of load or conditions, would inconvenience users
  behind NATs and degrade the initial user experience. While simpler to
  implement (we would not need to calculate the exact rate limit), it fails our
  design goal of minimizing friction for normal users.

- **Proxying thumbnail requests** The thumbnail requests from the frontend will
  remain anonymous on the API level. Since controlling search and related
  requests naturally throttles excessive thumbnail retrieval, we consider it
  unnecessary to further load the Nuxt server.

- **Only proxying the `/api` routes** This would make the setup more difficult
  as we would have to handle the challenge response from the `/api` routes to
  the fetch functions inside pinia stores.

## Dependencies

- **Feature Flags**:

  - `proxy_requests`

- **Infrastructure**: Cloudflare WAF rules, monitoring (Sentry, Cloudflare
  dashboard).

- **Tools & Packages**:
  - `ofetch`: This would be a great occasion to move away from `axios`. `ofetch`
    is used internally by Nuxt, so it's a good choice for the server proxying.
    The store requests can be made with Nuxt helper wrapper over `ofetch`,
    `$fetch`[^4].
  - `h3` for server route handling and session management.

## Accessibility and Privacy

- **Accessibility**:

  - Cloudflare challenges are generally accessible, but older browsers may fail.
  - Multilingual support helps non-English speakers.

- **Privacy**:
  - No personal data is stored, cookies are short-lived and anonymous.

## Rollback Strategy

- Set the `proxy_requests` feature flag to `off`. This will automatically
  deactivate the rate limiting rules, since the requests won't be going through
  the Nuxt server anymore.

[^1]:
    `Client requests` - Requests made by the frontend client to the API after
    the first page load. These requests are sent whenever a user submits a
    search, selects a filter or a search result. These requests are currently
    send directly to Openverse API without any authentication, because using the
    API credentials in the client code would expose them to the public.

[^2]:
    [Cloudflare multi-language support](https://developers.cloudflare.com/waf/reference/cloudflare-challenges/#multi-language-support)

[^3]:
    [Cloudflare rate limiting rules](https://developers.cloudflare.com/waf/rate-limiting-rules/)

[^4]: [Nuxt $fetch](https://nuxt.com/docs/api/utils/dollarfetch)
