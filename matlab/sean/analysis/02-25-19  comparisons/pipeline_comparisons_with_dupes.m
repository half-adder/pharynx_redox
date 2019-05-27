%% FLAGS
LOAD_DATA = 0;
USE_SYNTHETIC = 1;

REDO_CATA = 0;
REDO_1MID = 0;
REDO_2MID = 0;
REDO_REG  = 0;
REDO_SR   = 0;

GENERATE_TABLES = 1;
WRITE_TABLES = 1;

%% Load data
if LOAD_DATA
    [imTL, im410_1, im410_2, movement] = loadErrorData();
    
    if USE_SYNTHETIC
        disp('Generating Synthetic Data');
        nAnimal1 = size(im410_1, 3);
        pairsAnimal1 = unorderedPairs(1:nAnimal1);

        im410_1 = im410_1(:,:,pairsAnimal1(:,1));
        im410_2 = im410_1(:,:,pairsAnimal1(:,2));
        imTL = repmat(imTL(:,:,1), [1 1 size(im410_1, 3)]);
    end
end
%% Run all pipelines
if REDO_CATA
    disp('CATA Analysis');
    [i1_cata, i2_cata] = pipelineCata(imTL, im410_1, im410_2);
end

if REDO_1MID
    disp('1MID Analysis');
    [i1_1mid, i2_1mid] = pipelineOneMidlineTwoMasks(imTL, im410_1, im410_2);
end

if REDO_2MID
    disp('2MID Analysis');
    [i1_2mid, i2_2mid] = pipelineTwoMidlinesTwoMasks(imTL, im410_1, im410_2);
end

if REDO_REG
    disp('REG Analysis');
    [i1_reg, i2_reg, matchingVecs, mids1, mids2, scaled_bounds1, scaled_bounds2, fdObjs, dx, dy] = pipelineTwoMidlinesTwoMasksRegistration(imTL, im410_1, im410_2);
end

if REDO_SR
    disp('SR Analysis');
    [i1_sr, i2_sr, midlines1, midlines2, unreg_i1, unreg_i2, fdObjs] = pipelineSmoothRoughRegister(imTL, im410_1, im410_2);
end

%% Tables
if GENERATE_TABLES
abs_err_cata = regionMeansLong(abs(i1_cata - i2_cata), Constants.regions, 'AbsoluteError', 'Cata');
abs_err_1mid = regionMeansLong(abs(i1_1mid - i2_1mid), Constants.regions, 'AbsoluteError', '1 Midline');
abs_err_2mid = regionMeansLong(abs(i1_2mid - i2_2mid), Constants.regions, 'AbsoluteError', '2 Midlines');
abs_err_reg  = regionMeansLong(abs(i1_reg  - i2_reg),  Constants.regions, 'AbsoluteError', 'Registered');
abs_err_sr   = regionMeansLong(abs(i1_sr   - i2_sr),   Constants.regions, 'AbsoluteError', 'SmoothRough');

abs_err_table = vertcat(abs_err_cata, abs_err_1mid, abs_err_2mid, abs_err_reg, abs_err_sr);

int_1_cata = regionMeansLong(i1_cata, Constants.regions, 'I1', 'Cata');
int_1_1mid = regionMeansLong(i1_1mid, Constants.regions, 'I1', '1 Midline');
int_1_2mid = regionMeansLong(i1_2mid, Constants.regions, 'I1', '2 Midlines');
int_1_reg  = regionMeansLong(i1_reg,  Constants.regions, 'I1', 'Registered');
int_1_sr   = regionMeansLong(i1_sr,   Constants.regions, 'I1', 'SmoothRough');

int_1_table = vertcat(int_1_cata, int_1_1mid, int_1_2mid, int_1_reg, int_1_sr);

int_2_cata = regionMeansLong(i2_cata, Constants.regions, 'I2', 'Cata');
int_2_1mid = regionMeansLong(i2_1mid, Constants.regions, 'I2', '1 Midline');
int_2_2mid = regionMeansLong(i2_2mid, Constants.regions, 'I2', '2 Midlines');
int_2_reg  = regionMeansLong(i2_reg,  Constants.regions, 'I2', 'Registered');
int_2_sr  = regionMeansLong(i2_sr,    Constants.regions, 'I2', 'SmoothRough');

int_2_table = vertcat(int_2_cata, int_2_1mid, int_2_2mid, int_2_reg, int_2_sr);

rel_err_cata = regionMeansLong(abs(i1_cata - i2_cata) ./ ((i1_cata + i2_cata) / 2), Constants.regions, 'RelativeError', 'Cata');
rel_err_1mid = regionMeansLong(abs(i1_1mid - i2_1mid) ./ ((i1_1mid + i2_1mid) / 2), Constants.regions, 'RelativeError', '1 Midline');
rel_err_2mid = regionMeansLong(abs(i1_2mid - i2_2mid) ./ ((i1_2mid + i2_2mid) / 2), Constants.regions, 'RelativeError', '2 Midlines');
rel_err_reg  = regionMeansLong(abs(i1_reg  - i2_reg)  ./ ((i1_reg  + i2_reg)  / 2), Constants.regions, 'RelativeError', 'Registered');
rel_err_sr   = regionMeansLong(abs(i1_sr   - i2_sr)   ./ ((i1_reg  + i2_sr)   / 2), Constants.regions, 'RelativeError', 'SmoothRough');

rel_err_table = vertcat(rel_err_cata, rel_err_1mid, rel_err_2mid, rel_err_reg, rel_err_sr);

big_table = join(abs_err_table, join(int_1_table, join(int_2_table, rel_err_table)));

% % dist_means = regionMeansLong(d, Constants.regions, 'Distance', 'NA');
% dx_means = regionMeansLong(dx, Constants.regions, 'dX', 'NA');
% dy_means = regionMeansLong(dy, Constants.regions, 'dY', 'NA');
% 
% % big_table.Distance = repmat(dist_means.Distance, [4 1]);
% big_table.dx = repmat(dx_means.dX, [4 1]);
% big_table.dy = repmat(dy_means.dY, [4 1]);

end

if WRITE_TABLES
    if USE_SYNTHETIC
        writetable(big_table, '~/Desktop/synthetic_errors_table.csv');
    else
        writetable(big_table, '~/Desktop/normal_errors_table.csv');
    end
end

return
%% Plot Intensity
data_ = cat(3,...
    i1_cata, i1_1mid, i1_2mid, i1_reg);

labels_ = {'Cata', '1 Midline, Two Masks (Unregistered)', '2 Midlines, Two Masks (Unregistered)', '2 Midlines, Two Masks (Registered)'};
ylim_ = [0 20000];
plotMultiplePharynxData(data_, labels_, ylim_);
addRegionBoundsToPlot(gca, Constants.regions);

%% Rel Error
data_ = cat(3,...
    abs(i1_cata - i2_cata) ./ ((i1_cata + i2_cata) / 2),...
    abs(i1_1mid - i2_1mid) ./ ((i1_1mid + i2_1mid) / 2),...
    abs(i1_2mid - i2_2mid) ./ ((i1_2mid + i2_2mid) / 2),...
    abs(i1_reg - i2_reg) ./ ((i1_reg + i2_reg) / 2));

labels_ = {'Cata', '1 Midline, Two Masks (Unregistered)', '2 Midlines, Two Masks (Unregistered)', '2 Midlines, Two Masks (Registered)'};
ylim_ = [0 .1];
plotMultiplePharynxData(data_, labels_, ylim_);
addRegionBoundsToPlot(gca, Constants.regions);

%% Abs
data_ = cat(3,...
    abs(i1_cata - i2_cata),...
    abs(i1_1mid - i2_1mid),...
    abs(i1_2mid - i2_2mid),...
    abs(i1_reg - i2_reg));

labels_ = {'Cata', '1 Midline, Two Masks (Unregistered)', '2 Midlines, Two Masks (Unregistered)', '2 Midlines, Two Masks (Registered)'};
ylim_ = [0 500];
plotMultiplePharynxData(data_, labels_, ylim_);
addRegionBoundsToPlot(gca, Constants.regions);

%% Helpers
function pairs = unorderedPairs(v)
    [A,B] = meshgrid(v,v);
    c=cat(2,A',B');
    pairs=reshape(c,[],2);
    pairs(pairs(:,1)==pairs(:,2),:) = [];
end