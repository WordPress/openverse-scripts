<script setup lang="ts">
/**
 * This component receives an array of images as a prop, and
 * is responsible for displaying them as a grid.
 */

import type { CollectionComponentProps } from "#shared/types/collection-component-props"

import VImageResult from "~/components/VImageResult/VImageResult.vue"

withDefaults(defineProps<CollectionComponentProps<"image">>(), {
  relatedTo: "null",
})
</script>

<template>
  <ol
    class="image-grid flex flex-wrap gap-6 sm:gap-4"
    :aria-label="collectionLabel"
  >
    <VImageResult
      v-for="(image, idx) in results"
      :key="image.id"
      :image="image"
      :position="idx + 1"
      :search-term="searchTerm"
      aspect-ratio="intrinsic"
      :kind="kind"
      :related-to="relatedTo"
    />
  </ol>
</template>

<style scoped>
@screen md {
  .image-grid:after {
    /**
   * This keeps the last item in the results from expanding to fill
   * all available space, which can result in a final row with a
   * single, 100% wide image.
   */

    content: "";
    flex-grow: 999999999;
  }
}
</style>
