package io.github.carlsans.hrdpsweather

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The WCS answer parser behind the background download (the same grid format web/js/hrdps.js parseAAIGrid reads). */
class PrefetchTest {
    private val grid = "--wcs\r\nContent-Type: image/x-aaigrid\r\n\r\nncols 3\nnrows 2\nxllcorner -71\nyllcorner 46\n" +
        "cellsize 0.02\nNODATA_value -9999\n 1.5 2.5  3.5\n-9999 5 -1e30\n--wcs--\n"

    @Test fun parsesRowsNorthToSouthAndNoData() {
        val (ny, nx, d) = Prefetch.parseGrid(grid)!!
        assertEquals(2, ny); assertEquals(3, nx)
        assertEquals(1.5f, d[0]); assertEquals(3.5f, d[2]); assertEquals(5f, d[4])
        assertTrue(d[3].isNaN() && d[5].isNaN())
    }

    @Test fun rejectsAnswersThatAreNotGrids() {
        assertNull(Prefetch.parseGrid("<ServiceExceptionReport/>"))
        assertNull(Prefetch.parseGrid("ncols 2\nnrows 2\nNODATA_value -9999\n1 2\n3\n"))     // ragged rows
    }
}
