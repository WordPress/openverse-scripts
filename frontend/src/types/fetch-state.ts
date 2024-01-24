import type { ErrorCode } from "~/constants/errors"
import type { SupportedSearchType } from "~/constants/media"

/**
 * Describes the kind of API request that was made.
 */
export type RequestKind = "search" | "single-result" | "related" | "provider"

/**
 * This interface represents errors related to data-fetching from the API.
 * It has the information that can be used on the error page.
 */
export interface FetchingError {
  statusCode?: number
  statusMessage?: string
  message?: string
  /**
   * Axios error codes or custom error code like NO_RESULT.
   * @see frontend/src/constants/errors.ts
   */
  code: ErrorCode
  requestKind: RequestKind
  searchType: SupportedSearchType
  /**
   * Additional details about the error, e.g. the search term.
   */
  details?: Record<string, string>
}

export class VFetchingError extends Error {
  code: ErrorCode
  requestKind: RequestKind
  statusCode: number | undefined
  statusMessage: string | undefined
  searchType: SupportedSearchType
  details: Record<string, string> | undefined
  cause: Error | null
  constructor(public fetchingError: FetchingError, cause: Error | null = null) {
    super()
    this.name = "FetchingError"
    this.code = fetchingError.code
    this.statusCode = fetchingError.statusCode
    this.statusMessage = fetchingError.statusMessage
    this.requestKind = fetchingError.requestKind
    this.searchType = fetchingError.searchType
    this.details = fetchingError.details
    this.cause = cause
  }
}

export interface FetchState {
  isFetching: boolean
  hasStarted?: boolean
  isFinished?: boolean
  fetchingError: FetchingError | null
}
