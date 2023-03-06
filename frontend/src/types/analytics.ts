/**
 * compound type of all custom events sent from the site; Index with `EventName`
 * to get the type of the payload for a specific event.
 *
 * Conventions:
 * - Names should be in SCREAMING_SNAKE_CASE.
 * - Names should be imperative for events associated with user action.
 * - Names should be in past tense for events not associated with user action.
 * - Documentation must be the step to emit the event, followed by a line break.
 * - Questions that are answered by the event must be listed as bullet points.
 */
export type Events = {
  /**
   * Click on one of the images in the gallery on the homepage.
   *
   * - Do users know homepage images are links?
   * - Do users find these images interesting?
   * - Which set is most interesting for the users?
   */
  CLICK_HOME_GALLERY_IMAGE: {
    /** the set to which the image belongs */
    set: string
    /** the identifier of the image */
    identifier: string
  }
  /**
   * Navigate to a different page via an internal link.
   *
   * - How many users are navigating to other Openverse pages?
   * - What is the most common user path?
   * - Which content pages are the most popular?
   */
  VIEW_PAGE: {
    /** the name of the viewed page */
    name: string
    /** the path of the viewed page, overwrites `pathname` in mandatory props */
    pathname: string
    /** the path from which the user arrived here */
    fromPathname: string
  }
  /**
   * A page was rendered on the server by navigating to it from outside.
   *
   * - How many users are reaching Openverse by entering the URL?
   * - How many pages are server rendered vs client rendered?
   * - Which pages are common landing points?
   * - Are uses sharing search results?
   */
  SERVER_RENDERED: {
    /** the name of the viewed page **/
    name: string
  }
}

/**
 * the name of a custom event sent from the site
 */
export type EventName = keyof Events
