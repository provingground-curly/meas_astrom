import os
import sys
import math

import eups
import lsst.pex.policy as pexPolicy
from lsst.pex.logging import Log, Debug, LogRec, Prop
from lsst.pex.exceptions import LsstCppException
import lsst.afw.image as afwImg

import net as astromNet
import sip as astromSip
import sip.cleanBadPoints as cleanBadPoints


def determineWcs(policy, exposure, sourceSet, log=None):
    """Top level function for calculating a Wcs. 
    
    Given an initial guess at a Wcs (hidden inside an exposure) and a set of
    sources (sourceSet), use astrometry.net to confirm the Wcs, then calculate
    distortion terms.
    
    Input:
    policy:     An lsst.pex.policy.Policy object containing the parameters for the solver
    exposure    lsst.afw.image.Exposure representation of an image and a wcs 
                this provides the initial guess at position and plate scale
    sourceSet   A list of lsst.afw.detection.Source objects, indicating the pixel positions of 
                stars in the field
    log         A lsst.pex.logging.Log object (optional), used for printing progress
    """

    if log is not None:
        log.log(Log.INFO, "In determineWcs")


    #Short names
    exp = exposure
    srcSet = sourceSet
    
    outputWcsKey = policy.get('outputWcsKey')
    
    #Extract an initial guess wcs if available    
    wcsIn = exp.getWcs() #May be None
    if wcsIn is None:
        if log is not None:    
            log.log(log.WARN, "No wcs found on exposure. Doing blind solve")
    
    #Setup solver
    path=os.path.join(eups.productDir("astrometry_net_data"), "metadata.paf")
    solver = astromNet.GlobalAstrometrySolution(path)
    solver.allowDistortion(policy.get('allowDistortion'))
    matchThreshold = policy.get('matchThreshold')
    solver.setMatchThreshold(matchThreshold)

    if log is not None:
        log.log(log.DEBUG, "Setting starlist")
        log.log(log.DEBUG, "Setting numBrightObj")
    
    solver.setStarlist(srcSet)
    solver.setNumBrightObjects(policy.get('numBrightStars'))

    #Do a blind solve if we're told to, or if we don't have an input wcs
    doBlindSolve = policy.get('blindSolve') or (wcsIn is None)
    
    if log is not None:
        log.log(log.DEBUG, "Solving")
    
    if doBlindSolve or True:
        isSolved = solver.solve()
    else:
        isSolved = solver.solve(wcsIn)

    if log is not None:
        log.log(log.DEBUG, "Finished Solve step")
    if isSolved == False:
        if log is not None:
            log.log(log.WARN, "No solution found, using input Wcs")
        return None, wcsIn
    
    #
    #Do sip corrections
    #
    #First obtain the catalogue-listed positions of stars
    if log is not None:
        log.log(log.DEBUG, "Determining match objects")
    linearWcs = solver.getWcs()
    imgSizeInArcsec = getImageSizeInArcsec(srcSet, linearWcs)
    cat = solver.getCatalogue(2*imgSizeInArcsec) #Catalogue of nearby stars
    
    #Now generate a list of matching objects
    distInArcsec = policy.get('distanceForCatalogueMatchinArcsec')
    cleanParam = policy.get('cleaningParameter')

    matchList = matchSrcAndCatalogue(cat=cat, img=srcSet, wcs=linearWcs, 
        distInArcsec=distInArcsec, cleanParam=cleanParam)
            
    if len(matchList) == 0:
        if log is not None:
            log.log(Log.WARN, "No matches found between input source and catalogue.")
            log.log(Log.WARN, "Something in wrong. Defaulting to input wcs")
        return None, wcsIn
        
    if log is not None:
        log.log(Log.INFO, "%i objects out of %i match sources listed in catalogue" %(len(matchList), len(srcSet)))
        
    
    if policy.get('calculateSip'):
        #Now create a wcs with SIP polynomials
        maxScatter = policy.get('wcsToleranceInArcsec')
        maxSipOrder= policy.get('maxSipOrder')
        sipObject = astromSip.CreateWcsWithSip(matchList, linearWcs, maxScatter, maxSipOrder)

        if log is not None:
            log.log(Log.INFO, "Using %i th order SIP polynomial. Scatter is %.g arcsec" \
                %(sipObject.getOrder(), sipObject.getScatterInArcsec()))
    
    solver.reset()
    
    return [matchList, sipObject.getNewWcs()]
    
    
def getImageSizeInArcsec(srcSet, wcs):
    """ Get the approximate size of the image in arcseconds
    
    Input: 
    srcSet List of detected objects in the image (with pixel positions)
    wcs    Wcs converts pixel positions to ra/dec
    
    """
    xfunc = lambda x: x.getXAstrom()
    yfunc = lambda x: x.getYAstrom()
    
    x = map(xfunc, srcSet)
    y = map(yfunc, srcSet)
    
    minx = min(x)
    maxx = max(x)
    miny = min(y)
    maxy = max(y)
    
    llc = wcs.xyToRaDec(minx, miny)
    urc = wcs.xyToRaDec(maxx, maxy)
    
    deltaRa = urc[0]-llc[0]
    deltaDec = urc[1] - llc[1]
    
    #Approximately right
    dist = math.sqrt(deltaRa**2 + deltaDec**2)
    return dist*3600  #arcsec


def matchSrcAndCatalogue(cat=None, img=None, wcs=None, distInArcsec=1.0, cleanParam=3):
    """Given an input catalogue, match a list of objects in an image, given
    their x,y position and a wcs solution.
    
    Return: A list of x, y, dx and dy. Each element of the list is itself a list
    """
    
    if cat is None:
        raise RuntimeError("Catalogue list is not set")
    if img is None:
        raise RuntimeError("Image list is not set")
    if wcs is None:
        raise RuntimeError("wcs is not set")
    
        
    matcher = astromSip.MatchSrcToCatalogue(cat, img, wcs, distInArcsec)    
    matchList = matcher.getMatches()
    

    if matchList is None:
        raise RuntimeError("No matches found between image and catalogue")

    matchList = cleanBadPoints.clean(matchList, wcs, nsigma=cleanParam)
    return matchList

